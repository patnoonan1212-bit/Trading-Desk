"""Historical CME data from Databento (dataset GLBX.MDP3).

We request every outright of a product with its parent symbol (e.g. 'MES.FUT'), drop calendar spreads, and
let the Data Agent build continuous contracts itself. Downloads are split by month so large ranges don't
exhaust memory, and the cost is checked before anything is downloaded.

The API key is read from the DATABENTO_API_KEY environment variable; it is never stored in code or config.
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from tradingdesk.data.providers.base import BAR_COLUMNS, HistoricalProvider, ProviderError, empty_bars, utc
from tradingdesk.data.symbols import is_outright

log = logging.getLogger(__name__)


def _trades_to_minute_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Turn tick-by-tick trades into 1-minute OHLCV bars per contract."""
    out = []
    for sym, g in df.groupby("symbol"):
        r = g["price"].resample("1min")
        bars = pd.DataFrame({"open": r.first(), "high": r.max(), "low": r.min(), "close": r.last(), "volume": g["size"].resample("1min").sum()})
        bars = bars.dropna(subset=["open"])
        bars["symbol"] = sym
        out.append(bars)
    if not out:
        return empty_bars()
    res = pd.concat(out).reset_index(names="ts")
    return res[BAR_COLUMNS]


class DatabentoProvider(HistoricalProvider):
    name = "databento"

    def __init__(self, dataset: str = "GLBX.MDP3", api_key: str | None = None, client=None) -> None:
        self.dataset = dataset
        self._client = client
        self._key = api_key or os.environ.get("DATABENTO_API_KEY")

    @property
    def client(self):
        if self._client is None:
            if not self._key:
                raise ProviderError("DATABENTO_API_KEY is not set. Add it as an environment variable or GitHub secret.")
            try:
                import databento as db
            except ImportError as e:  # pragma: no cover
                raise ProviderError("The databento package is not installed: pip install 'tradingdesk[databento]'") from e
            self._client = db.Historical(self._key)
        return self._client

    def _range_kwargs(self, root, start, end, schema):
        return dict(
            dataset=self.dataset,
            symbols=[f"{root}.FUT"],
            stype_in="parent",
            schema=schema,
            start=pd.Timestamp(start).isoformat(),
            end=pd.Timestamp(end).isoformat(),
        )

    def estimate_cost(self, root, start, end, schema="ohlcv-1m") -> float:
        return float(self.client.metadata.get_cost(**self._range_kwargs(root, start, end, schema)))

    def fetch_bars(self, root, start, end, schema="ohlcv-1m") -> pd.DataFrame:
        start, end = utc(start), utc(end)
        chunks = []
        edges = list(pd.date_range(start.normalize(), end, freq="MS", tz="UTC"))
        edges = sorted({start, *[e for e in edges if start < e < end], end})
        for a, b in zip(edges[:-1], edges[1:], strict=True):
            log.info("Databento: %s %s %s to %s", root, schema, a.date(), b.date())
            store = self.client.timeseries.get_range(**self._range_kwargs(root, a, b, schema))
            df = store.to_df()
            if df.empty:
                continue
            df = df[df["symbol"].map(is_outright)]
            if schema == "trades":
                chunks.append(_trades_to_minute_bars(df))
            else:
                df = df.reset_index().rename(columns={"ts_event": "ts"})
                chunks.append(df[BAR_COLUMNS])
        if not chunks:
            return empty_bars()
        bars = pd.concat(chunks, ignore_index=True)
        bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
        return bars.sort_values(["ts", "symbol"]).reset_index(drop=True)
