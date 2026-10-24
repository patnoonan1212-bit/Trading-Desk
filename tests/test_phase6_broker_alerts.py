"""Email alerts, the Tradovate client, mirroring the desk at the broker, and live-mode safety."""

import pandas as pd
import pytest

from tradingdesk.alerts import EmailAlerter
from tradingdesk.brokers.tradovate import (
    Credentials,
    TradovateClient,
    TradovateError,
    _frames,
    chart_bars,
    tradovate_symbol,
)
from tradingdesk.config import load_settings
from tradingdesk.live.mirror import BrokerMirror, Target


# ----- email ----------------------------------------------------------------------------------------------
class FakeSMTP:
    sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        if p == "bad":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"no")

    def send_message(self, m):
        FakeSMTP.sent.append(m)


def test_email_alerts_send_and_report_failures():
    a = EmailAlerter("me@x.com", "desk@gmail.com", "app-pass", smtp_factory=FakeSMTP)
    assert a.configured and a.send("Hi", "body") and FakeSMTP.sent[-1]["To"] == "me@x.com"
    bad = EmailAlerter("me@x.com", "desk@gmail.com", "bad", smtp_factory=FakeSMTP)
    assert not bad.send("Hi", "body") and "Authentication" in bad.last_error
    assert not EmailAlerter("", "", "").configured


# ----- a pretend Tradovate --------------------------------------------------------------------------------
class FakeTradovate:
    """Answers like Tradovate's REST API: market orders fill at once, stop orders wait."""

    def __init__(self, net_liq=50000.0, penalty=False):
        self.positions, self.orders, self.versions, self.calls = {}, {}, {}, []
        self.contracts = {1: "MESZ6", 2: "MESH7", 3: "MGCZ6"}
        self.net_liq, self.penalty, self.next_id = net_liq, penalty, 100

    def cid(self, name):
        return next(k for k, v in self.contracts.items() if v == name)

    def __call__(self, method, url, body, headers):
        path = url.split("/v1", 1)[1]
        self.calls.append((method, path, body))
        if path == "/auth/accesstokenrequest":
            if self.penalty and "p-ticket" not in body:
                return 200, {"p-ticket": "T", "p-time": 1}
            return 200, {"accessToken": "tok", "mdAccessToken": "md", "expirationTime": "2099-01-01T00:00:00Z"}
        if headers.get("Authorization") != "Bearer tok":
            return 401, {"errorText": "unauthorized"}
        if path == "/account/list":
            return 200, [{"id": 7, "name": "DEMO123"}]
        if path == "/position/list":
            return 200, [{"accountId": 7, "contractId": c, "netPos": q} for c, q in self.positions.items()]
        if path.startswith("/contract/item"):
            i = int(path.split("=")[1])
            return 200, {"id": i, "name": self.contracts[i]}
        if path == "/order/list":
            return 200, list(self.orders.values())
        if path == "/orderVersion/list":
            return 200, list(self.versions.values())
        if path == "/cashBalance/getcashbalancesnapshot":
            return 200, {"netLiq": self.net_liq}
        if path == "/order/placeorder":
            c = self.cid(body["symbol"])
            sign = 1 if body["action"] == "Buy" else -1
            if body["orderType"] == "Market":
                self.positions[c] = self.positions.get(c, 0) + sign * body["orderQty"]
                if not self.positions[c]:
                    del self.positions[c]
                return 200, {"orderId": 1}
            self.next_id += 1
            oid = self.next_id
            self.orders[oid] = {"id": oid, "accountId": 7, "contractId": c, "ordStatus": "Working", "action": body["action"]}
            self.versions[oid] = {"id": oid, "orderId": oid, "orderType": body["orderType"], "orderQty": body["orderQty"], "stopPrice": body.get("stopPrice")}
            return 200, {"orderId": oid}
        if path == "/order/modifyorder":
            v = self.versions[body["orderId"]]
            v.update(orderQty=body["orderQty"], stopPrice=body.get("stopPrice"), id=v["id"] + 1000)
            return 200, {"commandId": 1}
        if path == "/order/cancelorder":
            self.orders.pop(body["orderId"], None)
            return 200, {"commandId": 1}
        return 404, {"errorText": "unknown " + path}


CREDS = Credentials("u", "p", "Desk", "1.0", "cid", "sec")


def test_client_logs_in_waits_when_asked_and_reads_the_account():
    fake = FakeTradovate(penalty=True)
    waited = []
    c = TradovateClient("demo", CREDS, http=fake, sleep=waited.append)
    assert c.accounts()[0]["name"] == "DEMO123"
    assert waited == [1.0] and c.token == "tok"
    assert tradovate_symbol("MESZ26") == "MESZ6"


def test_client_errors_are_plain(monkeypatch):
    for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID", "TRADOVATE_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(TradovateError, match="missing"):
        TradovateClient("demo", http=FakeTradovate()).login()
    with pytest.raises(ValueError):
        TradovateClient("prod", CREDS)
    fake = FakeTradovate()
    c = TradovateClient("demo", CREDS, http=fake)
    with pytest.raises(TradovateError, match="unknown"):
        c.call("GET", "/nothing/here")


def test_market_data_frames_become_bars():
    raw = 'a[{"e":"chart","d":{"charts":[{"id":1,"bars":[{"timestamp":"2026-10-23T14:00:00.000Z","open":5000,"high":5001,"low":4999,"close":5000.5,"upVolume":30,"downVolume":12}]}]}}]'
    bars = chart_bars(_frames(raw))
    assert bars.iloc[0]["volume"] == 42 and bars.iloc[0]["ts"] == pd.Timestamp("2026-10-23 14:00", tz="UTC")
    assert _frames("h") == [] and _frames("o") == []


# ----- mirroring ------------------------------------------------------------------------------------------
def _settings(tmp_path, dry_run=False, **broker):
    s = load_settings(mode="paper")
    s.contracts = ["MES", "MGC"]
    s.broker.name, s.broker.dry_run = "tradovate", dry_run
    for k, v in broker.items():
        setattr(s.broker, k, v)
    s.paper.state_path = tmp_path
    return s


NOW = pd.Timestamp("2026-10-23 15:00", tz="UTC")


FIVE_MIN_AGO = NOW - pd.Timedelta(minutes=5)


def _t(qty=3, stop=4990.0, contract="MESZ6", opened=FIVE_MIN_AGO, last=5001.0, trade_id=1):
    return Target("MES", contract, qty, stop, trade_id, opened, 5000.0, 10.0, last)


def _sync(m, want, monkeypatch, data_end=NOW):
    import tradingdesk.live.mirror as mm

    monkeypatch.setattr(mm, "targets", lambda engine, data: want)
    data = {"MES": pd.DataFrame({"close": [1.0]}, index=[data_end])}
    return m.sync(None, data, NOW)


def test_mirror_copies_position_and_places_one_real_stop(tmp_path, monkeypatch):
    fake = FakeTradovate()
    m = BrokerMirror(_settings(tmp_path), tmp_path, TradovateClient("demo", CREDS, http=fake))
    r = _sync(m, {"MES": _t()}, monkeypatch)
    assert fake.positions == {1: 3}
    stops = [v for v in fake.versions.values() if v["orderType"] == "Stop"]
    assert len(stops) == 1 and stops[0]["stopPrice"] == 4990.0 and stops[0]["orderQty"] == 3
    assert not r.problems
    # Next update: the desk trailed its stop and took 1 contract off.
    r = _sync(m, {"MES": _t(qty=2, stop=4998.0)}, monkeypatch)
    assert fake.positions == {1: 2}
    v = next(iter(fake.versions.values()))
    assert v["stopPrice"] == 4998.0 and v["orderQty"] == 2 and len(fake.orders) == 1
    # The desk closed the trade: broker goes flat and the stop is cancelled.
    _sync(m, {}, monkeypatch)
    assert fake.positions == {} and fake.orders == {}


def test_mirror_rolls_to_the_new_contract(tmp_path, monkeypatch):
    fake = FakeTradovate()
    fake.positions = {1: 2}
    m = BrokerMirror(_settings(tmp_path), tmp_path, TradovateClient("demo", CREDS, http=fake))
    _sync(m, {"MES": _t(qty=2, contract="MESH7", opened=NOW - pd.Timedelta(days=3))}, monkeypatch)
    assert fake.positions == {2: 2}       # old month closed, new month opened (an existing position, not a new entry)


def test_late_entries_are_skipped_but_exits_always_copied(tmp_path, monkeypatch):
    fake = FakeTradovate()
    m = BrokerMirror(_settings(tmp_path), tmp_path, TradovateClient("demo", CREDS, http=fake))
    r = _sync(m, {"MES": _t(opened=NOW - pd.Timedelta(minutes=45))}, monkeypatch)
    assert fake.positions == {} and "Didn't copy" in r.skipped[0]
    assert _sync(m, {"MES": _t(opened=NOW - pd.Timedelta(minutes=45))}, monkeypatch).skipped == []   # reported once
    r = _sync(m, {"MES": _t(last=5008.0, trade_id=2)}, monkeypatch)                                   # ran 0.8R already
    assert fake.positions == {} and "ran" in r.skipped[0]


def test_safety_net_closes_everything_on_a_bad_day(tmp_path, monkeypatch):
    fake = FakeTradovate(net_liq=50000)
    m = BrokerMirror(_settings(tmp_path), tmp_path, TradovateClient("demo", CREDS, http=fake))
    _sync(m, {"MES": _t()}, monkeypatch)
    fake.net_liq = 47500                       # -5% today, past the 4% limit
    r = _sync(m, {"MES": _t()}, monkeypatch)
    assert r.halted and fake.positions == {} and fake.orders == {}
    assert "Safety net" in r.problems[0]


def test_dry_run_sends_nothing_and_reports_only_changes(tmp_path, monkeypatch):
    m = BrokerMirror(_settings(tmp_path, dry_run=True), tmp_path)
    r = _sync(m, {"MES": _t()}, monkeypatch)
    assert any(a.startswith("Buy 3 MESZ6") for a in r.actions) and any("Place a stop" in a for a in r.actions)
    assert _sync(m, {"MES": _t()}, monkeypatch).actions == []
    r = _sync(m, {"MES": _t(stop=4995.0)}, monkeypatch)
    assert r.actions == ["Move the MESZ6 stop to 4995 for 3 contract(s)."]


def test_order_size_cap_holds_at_the_broker(tmp_path, monkeypatch):
    fake = FakeTradovate()
    s = _settings(tmp_path)
    s.execution.max_contracts_per_order = 2
    r = _sync(BrokerMirror(s, tmp_path, TradovateClient("demo", CREDS, http=fake)), {"MES": _t()}, monkeypatch)
    assert fake.positions == {} and "safety limit" in r.problems[0]


# ----- live mode can't be switched on by accident ---------------------------------------------------------
def test_live_needs_every_switch(monkeypatch):
    for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID", "TRADOVATE_SECRET"):
        monkeypatch.setenv(k, "x")
    with pytest.raises(ValueError, match="broker is not Tradovate live"):
        load_settings(mode="live")
    with pytest.raises(ValueError, match="mode is not 'live'"):
        load_settings(broker={"name": "tradovate", "environment": "live"})
    with pytest.raises(ValueError, match="delayed feed"):
        load_settings(mode="live", broker={"name": "tradovate", "environment": "live"})
    s = load_settings(mode="live", broker={"name": "tradovate", "environment": "live"},
                      paper={"data_provider": "tradovate"})
    assert s.mode == "live"


# ----- live-readiness checklist -----------------------------------------------------------------------------
def test_checklist_starts_empty_and_passes_only_on_real_evidence(tmp_path):
    import json

    import numpy as np

    from tradingdesk import readiness

    s = load_settings(mode="paper")
    s.paper.learning_file = tmp_path / "learning.json"
    items = readiness.evaluate(s, tmp_path, NOW, email_ready=False)
    assert not readiness.ready(items) and sum(i.passed for i in items) == 1   # only "kill switch off"
    # Build a record that should pass everything.
    start = NOW - pd.Timedelta(weeks=10)
    runs = [(NOW - pd.Timedelta(minutes=i)).isoformat() for i in range(200)]
    (tmp_path / "state.json").write_text(json.dumps({"started_at": start.isoformat(), "run_times": runs, "alert_times": []}))
    rng = np.random.default_rng(1)
    r = rng.normal(0.3, 1.0, 80)
    pd.DataFrame({"r": r, "net_pnl": r * 700}).to_csv(tmp_path / "trades.csv", index=False)
    eq = pd.Series(50000 + np.cumsum(r * 700), index=pd.date_range(start, periods=80, freq="12h"), name="equity")
    eq.to_csv(tmp_path / "equity.csv")
    (tmp_path / "mirror.json").write_text(json.dumps({"real_since": (NOW - pd.Timedelta(days=20)).isoformat()}))
    s.paper.learning_file.write_text(json.dumps({"test_from": "2025-01-01", "baseline": {"trades": 300, "total_r": 25.0}}))
    s.paper.data_provider = "tradovate"
    items = readiness.evaluate(s, tmp_path, NOW, email_ready=True)
    assert readiness.ready(items), [i for i in items if not i.passed]
    s.risk.kill_switch = True
    assert not readiness.ready(readiness.evaluate(s, tmp_path, NOW, email_ready=True))
