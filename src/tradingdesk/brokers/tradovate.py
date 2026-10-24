"""Tradovate: the broker connection (orders, positions, account) and real-time prices.

Written against Tradovate's published API (REST for trading, a WebSocket for market data). Until it has run against
a real Tradovate account it should be treated as unproven: the "Check Tradovate" button logs in, lists accounts,
positions and orders, and fetches a few minutes of prices, all read-only, and shows exactly what came back.

Credentials come only from environment variables (GitHub secrets):
    TRADOVATE_USERNAME, TRADOVATE_PASSWORD   your Tradovate login
    TRADOVATE_APP_ID, TRADOVATE_APP_VERSION  the name/version you give the API key (any text, e.g. "TradingDesk", "1.0")
    TRADOVATE_CID, TRADOVATE_SECRET          the API key's client id and secret (Tradovate: Settings -> API Access)
    TRADOVATE_DEVICE_ID                      optional: any fixed text that identifies this desk

Demo (practice money) and live (real money) are separate servers. Live is only ever used when the settings say
`mode: live` and `broker.environment: live` and the live-readiness checklist has passed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import pandas as pd

from tradingdesk.contracts import MONTH_CODES
from tradingdesk.data.providers.base import BAR_COLUMNS, HistoricalProvider, ProviderError, empty_bars, utc
from tradingdesk.data.symbols import active_contracts, parse

log = logging.getLogger(__name__)

REST = {"demo": "https://demo.tradovateapi.com/v1", "live": "https://live.tradovateapi.com/v1"}
MARKET_DATA = {"demo": "wss://md-demo.tradovateapi.com/v1/websocket", "live": "wss://md.tradovateapi.com/v1/websocket"}


class TradovateError(RuntimeError):
    pass


def tradovate_symbol(contract: str) -> str:
    """Our contract names use two-digit years (MESZ26); Tradovate uses one digit (MESZ6)."""
    p = parse(contract)
    if p is None:
        return contract
    root, year, month = p
    return f"{root}{MONTH_CODES[month - 1]}{year % 10}"


@dataclass
class Credentials:
    username: str
    password: str
    app_id: str
    app_version: str
    cid: str
    secret: str
    device_id: str = "trading-desk"

    @classmethod
    def from_env(cls) -> Credentials | None:
        e = os.environ
        need = ["TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID", "TRADOVATE_SECRET"]
        if not all(e.get(k) for k in need):
            return None
        return cls(e["TRADOVATE_USERNAME"], e["TRADOVATE_PASSWORD"], e.get("TRADOVATE_APP_ID", "TradingDesk"),
                   e.get("TRADOVATE_APP_VERSION", "1.0"), e["TRADOVATE_CID"], e["TRADOVATE_SECRET"],
                   e.get("TRADOVATE_DEVICE_ID", "trading-desk"))

    @staticmethod
    def missing() -> list[str]:
        return [k for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID", "TRADOVATE_SECRET") if not os.environ.get(k)]


def _http(method: str, url: str, body: dict | None, headers: dict, timeout: float = 20.0) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"errorText": raw.decode(errors="replace")[:200]}


class TradovateClient:
    """Thin wrapper over Tradovate's REST API. `http` can be replaced in tests."""

    def __init__(self, environment: str = "demo", creds: Credentials | None = None, http=_http, sleep=time.sleep) -> None:
        if environment not in REST:
            raise ValueError("environment must be demo or live")
        self.environment = environment
        self.base = REST[environment]
        self.creds = creds or Credentials.from_env()
        self.http = http
        self.sleep = sleep
        self.token = ""
        self.md_token = ""
        self.expires: pd.Timestamp | None = None
        self._contracts: dict[int, str] = {}

    # ----- auth -----------------------------------------------------------------------------------------
    def login(self) -> None:
        if self.creds is None:
            raise TradovateError(f"Tradovate login details are missing: {', '.join(Credentials.missing())}")
        c = self.creds
        body = {"name": c.username, "password": c.password, "appId": c.app_id, "appVersion": c.app_version,
                "cid": c.cid, "sec": c.secret, "deviceId": c.device_id}
        for _ in range(3):
            status, r = self.http("POST", f"{self.base}/auth/accesstokenrequest", body, {})
            if isinstance(r, dict) and r.get("p-ticket"):
                # Too many logins: Tradovate asks us to wait, then retry with the ticket it gave.
                self.sleep(min(float(r.get("p-time", 5)), 60))
                body["p-ticket"] = r["p-ticket"]
                continue
            if status != 200 or not isinstance(r, dict) or not r.get("accessToken"):
                raise TradovateError(f"Tradovate login failed: {(r or {}).get('errorText', status) if isinstance(r, dict) else status}")
            self.token, self.md_token = r["accessToken"], r.get("mdAccessToken", "")
            self.expires = pd.Timestamp(r["expirationTime"]) if r.get("expirationTime") else None
            return
        raise TradovateError("Tradovate kept asking us to wait before logging in; try again in a few minutes.")

    def _ensure(self) -> None:
        if not self.token:
            self.login()
        elif self.expires is not None and self.expires - pd.Timestamp.now(tz="UTC") < pd.Timedelta(minutes=10):
            status, r = self.http("GET", f"{self.base}/auth/renewaccesstoken", None, {"Authorization": f"Bearer {self.token}"})
            if status == 200 and isinstance(r, dict) and r.get("accessToken"):
                self.token = r["accessToken"]
                self.expires = pd.Timestamp(r["expirationTime"]) if r.get("expirationTime") else None
            else:
                self.login()

    def call(self, method: str, path: str, body: dict | None = None) -> object:
        self._ensure()
        status, r = self.http(method, f"{self.base}{path}", body, {"Authorization": f"Bearer {self.token}"})
        if status == 401:  # token expired early: log in again once
            self.login()
            status, r = self.http(method, f"{self.base}{path}", body, {"Authorization": f"Bearer {self.token}"})
        if status != 200:
            raise TradovateError(f"Tradovate {path} failed ({status}): {r.get('errorText', '') if isinstance(r, dict) else r}")
        return r

    # ----- reading ----------------------------------------------------------------------------------------
    def accounts(self) -> list[dict]:
        return list(self.call("GET", "/account/list"))

    def positions(self) -> list[dict]:
        return list(self.call("GET", "/position/list"))

    def orders(self) -> list[dict]:
        return list(self.call("GET", "/order/list"))

    def order_versions(self) -> list[dict]:
        return list(self.call("GET", "/orderVersion/list"))

    def cash_balance(self, account_id: int) -> dict:
        r = self.call("POST", "/cashBalance/getcashbalancesnapshot", {"accountId": account_id})
        return r if isinstance(r, dict) else {}

    def contract_name(self, contract_id: int) -> str:
        if contract_id not in self._contracts:
            r = self.call("GET", f"/contract/item?id={contract_id}")
            self._contracts[contract_id] = r.get("name", str(contract_id)) if isinstance(r, dict) else str(contract_id)
        return self._contracts[contract_id]

    def working_orders(self) -> list[dict]:
        """Open orders with their current price, size and type (from the latest order version)."""
        latest: dict[int, dict] = {}
        for v in self.order_versions():
            oid = v.get("orderId")
            if oid is not None and (oid not in latest or v.get("id", 0) > latest[oid].get("id", 0)):
                latest[oid] = v
        out = []
        for o in self.orders():
            if o.get("ordStatus") not in ("Working", "PendingNew", "PendingReplace"):
                continue
            v = latest.get(o["id"], {})
            out.append({"id": o["id"], "accountId": o.get("accountId"), "contract": self.contract_name(o["contractId"]),
                        "action": o.get("action"), "type": v.get("orderType"), "qty": v.get("orderQty"),
                        "price": v.get("price"), "stopPrice": v.get("stopPrice")})
        return out

    # ----- trading ----------------------------------------------------------------------------------------
    def place_order(self, account: dict, symbol: str, action: str, qty: int, order_type: str = "Market",
                    price: float | None = None, stop_price: float | None = None) -> int:
        body = {"accountSpec": account["name"], "accountId": account["id"], "action": action, "symbol": symbol,
                "orderQty": int(qty), "orderType": order_type, "isAutomated": True}
        if price is not None:
            body["price"] = price
        if stop_price is not None:
            body["stopPrice"] = stop_price
        if order_type != "Market":
            body["timeInForce"] = "GTC"
        r = self.call("POST", "/order/placeorder", body)
        if not isinstance(r, dict) or "orderId" not in r:
            raise TradovateError(f"Order refused by Tradovate: {(r or {}).get('failureText') or (r or {}).get('failureReason') if isinstance(r, dict) else r}")
        return int(r["orderId"])

    def modify_order(self, order_id: int, qty: int, order_type: str, stop_price: float | None = None, price: float | None = None) -> None:
        body = {"orderId": order_id, "orderQty": int(qty), "orderType": order_type, "isAutomated": True}
        if stop_price is not None:
            body["stopPrice"] = stop_price
        if price is not None:
            body["price"] = price
        self.call("POST", "/order/modifyorder", body)

    def cancel_order(self, order_id: int) -> None:
        self.call("POST", "/order/cancelorder", {"orderId": order_id, "isAutomated": True})


# ----- real-time prices ----------------------------------------------------------------------------------
def _frames(raw: str) -> list[dict]:
    """Tradovate WebSocket frames: 'o' open, 'h' heartbeat, 'a[...]' data, 'c' close."""
    if not raw or raw[0] != "a":
        return []
    try:
        return list(json.loads(raw[1:]))
    except json.JSONDecodeError:
        return []


def chart_bars(messages: list[dict]) -> pd.DataFrame:
    """1-minute bars from Tradovate chart messages."""
    rows = []
    for m in messages:
        if m.get("e") != "chart":
            continue
        for ch in m.get("d", {}).get("charts", []):
            for b in ch.get("bars", []):
                rows.append({"ts": pd.Timestamp(b["timestamp"]), "open": b["open"], "high": b["high"], "low": b["low"],
                             "close": b["close"], "volume": float(b.get("upVolume", 0)) + float(b.get("downVolume", 0))})
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates("ts", keep="last").sort_values("ts")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.reset_index(drop=True)


class TradovateMarketData(HistoricalProvider):
    """Real-time 1-minute bars from Tradovate (needs a market-data subscription on the Tradovate account)."""

    name = "tradovate"

    def __init__(self, client: TradovateClient, contracts_per_root: int = 2, timeout: float = 20.0, connect=None) -> None:
        self.client = client
        self.contracts_per_root = contracts_per_root
        self.timeout = timeout
        self._connect = connect

    def _bars(self, symbol: str, n: int) -> pd.DataFrame:
        import asyncio

        return asyncio.run(self._bars_async(symbol, n))

    async def _bars_async(self, symbol: str, n: int) -> pd.DataFrame:
        import asyncio

        self.client._ensure()
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        msgs: list[dict] = []
        async with connect(MARKET_DATA[self.client.environment]) as ws:
            await asyncio.wait_for(ws.recv(), self.timeout)                     # 'o'
            await ws.send(f"authorize\n0\n\n{self.client.md_token or self.client.token}")
            req = {"symbol": symbol, "chartDescription": {"underlyingType": "MinuteBar", "elementSize": 1,
                                                          "elementSizeUnit": "UnderlyingUnits", "withHistogram": False},
                   "timeRange": {"asMuchAsElements": int(n)}}
            await ws.send(f"md/getchart\n1\n\n{json.dumps(req)}")
            deadline = asyncio.get_event_loop().time() + self.timeout
            while asyncio.get_event_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), self.timeout)
                if raw == "h":
                    await ws.send("[]")
                    continue
                for f in _frames(raw):
                    if f.get("i") == 0 and f.get("s", 200) != 200:
                        raise ProviderError(f"Tradovate market data refused the login ({f.get('d')})")
                    if f.get("i") == 1 and f.get("s", 200) != 200:
                        raise ProviderError(f"Tradovate refused the chart request for {symbol} ({f.get('d')})")
                    msgs.append(f)
                    charts = f.get("d", {}).get("charts", []) if f.get("e") == "chart" else []
                    if any(c.get("eoh") for c in charts):                       # end of history: done
                        return chart_bars(msgs)
        return chart_bars(msgs)

    def fetch_bars(self, root, start, end, schema="ohlcv-1m") -> pd.DataFrame:
        start, end = utc(start), utc(end)
        n = int(min(5000, max(10, (end - start).total_seconds() / 60)))
        out = []
        for c in active_contracts(root, end, self.contracts_per_root):
            df = self._bars(tradovate_symbol(c), n)
            now = pd.Timestamp.now(tz="UTC")
            df = df[(df["ts"] >= start) & (df["ts"] < end) & (df["ts"] + pd.Timedelta(minutes=1) <= now)]  # the minute still forming is left out
            if len(df):
                out.append(df.assign(symbol=c))
        if not out:
            return empty_bars()
        return pd.concat(out, ignore_index=True)[BAR_COLUMNS]
