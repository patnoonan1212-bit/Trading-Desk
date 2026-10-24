"""Free, delayed live prices from Yahoo Finance, for paper trading.

CME prices on Yahoo are about 10 minutes behind and only the last ~30 days of 1-minute bars are available, so
this source is for paper trading only: it proves the live pipeline works without paying for data. Real-time data
comes from Tradovate before any live-money decision.

We ask for individual contract months (e.g. 'MESZ25.CME') rather than Yahoo's rolling 'MES=F', so the Data
Agent can roll from one month to the next on volume exactly as it does with Databento history. If a contract
symbol returns nothing, we fall back to 'MES=F' and label it with the expected front month.

The bar still being formed (the most recent minute) is dropped; it is picked up complete on the next run.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from tradingdesk.data.providers.base import BAR_COLUMNS, HistoricalProvider, ProviderError, empty_bars, utc
from tradingdesk.data.symbols import active_contracts

log = logging.getLogger(__name__)

EXCHANGE_SUFFIX = {"MES": "CME", "MNQ": "CME", "MCL": "NYM", "MGC": "CMX"}
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
MAX_DAYS_BACK = 29   # Yahoo keeps about 30 days of 1-minute bars
CHUNK_DAYS = 7       # and serves at most ~8 days per request


def yahoo_symbol(contract: str, root: str) -> str:
    return f"{contract}.{EXCHANGE_SUFFIX[root]}"


def parse_chart(payload: dict) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Turn a Yahoo chart response into bars (ts, open, high, low, close, volume) and the last trade time."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=BAR_COLUMNS[:1] + BAR_COLUMNS[2:]), None
    r = result[0]
    last_trade = r.get("meta", {}).get("regularMarketTime")
    stamps = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    df = pd.DataFrame({"ts": pd.to_datetime(stamps, unit="s", utc=True), **{k: q.get(k, [None] * len(stamps)) for k in ("open", "high", "low", "close", "volume")}})
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0).astype(float)
    df["ts"] = df["ts"].dt.floor("min")
    lt = pd.Timestamp(last_trade, unit="s", tz="UTC") if last_trade else None
    if lt is not None:
        df = df[df["ts"] + pd.Timedelta(minutes=1) <= lt]  # the minute still forming is incomplete
    return df.reset_index(drop=True), lt


class YahooProvider(HistoricalProvider):
    name = "yahoo"

    def __init__(self, contracts_per_root: int = 2, timeout: float = 20.0, retries: int = 3) -> None:
        self.contracts_per_root = contracts_per_root
        self.timeout = timeout
        self.retries = retries
        self.last_trade: dict[str, pd.Timestamp] = {}
        self.used_fallback: set[str] = set()

    # Network access is one small method so tests can replace it.
    def _get(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
        params = urllib.parse.urlencode({"interval": "1m", "period1": int(start.timestamp()), "period2": int(end.timestamp()),
                                         "includePrePost": "true"})
        last_err: Exception | None = None
        for attempt in range(self.retries):
            host = HOSTS[attempt % len(HOSTS)]
            req = urllib.request.Request(f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}",
                                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:  # unknown symbol
                    return {}
                last_err = e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            time.sleep(2**attempt)
        raise ProviderError(f"Yahoo did not answer for {symbol}: {last_err}")

    def _fetch_symbol(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        parts = []
        t = start
        while t < end:
            t2 = min(end, t + pd.Timedelta(days=CHUNK_DAYS))
            df, lt = parse_chart(self._get(symbol, t, t2))
            if lt is not None:
                self.last_trade[symbol] = max(lt, self.last_trade.get(symbol, lt))
            parts.append(df)
            t = t2
        parts = [p for p in parts if len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    def fetch_bars(self, root: str, start, end, schema: str = "ohlcv-1m") -> pd.DataFrame:
        if schema != "ohlcv-1m":
            raise ProviderError("The free live feed only has 1-minute bars.")
        now = pd.Timestamp.now(tz="UTC")
        end = min(utc(end), now)
        start = max(utc(start), now - pd.Timedelta(days=MAX_DAYS_BACK))
        if start >= end:
            return empty_bars()
        out = []
        contracts = active_contracts(root, end, self.contracts_per_root)
        for c in contracts:
            df = self._fetch_symbol(yahoo_symbol(c, root), start, end)
            if len(df):
                out.append(df.assign(symbol=c))
        if not out:
            # Fall back to Yahoo's rolling front-month series, labelled with the month we expect it to be.
            df = self._fetch_symbol(f"{root}=F", start, end)
            if len(df):
                self.used_fallback.add(root)
                log.warning("%s: contract months unavailable on Yahoo; using the rolling %s=F series.", root, root)
                out.append(df.assign(symbol=contracts[0]))
        if not out:
            return empty_bars()
        return pd.concat(out, ignore_index=True)[BAR_COLUMNS].sort_values(["ts", "symbol"]).reset_index(drop=True)
