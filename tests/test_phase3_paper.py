"""Execution Agent, free live feed parsing, paper trading by replay, and the Watchdog."""

import json

import pandas as pd
import pytest

from tradingdesk.agents.execution import ExecutionAgent, LiveTradingNotAvailable, make_broker
from tradingdesk.agents.watchdog import Watchdog, open_minutes
from tradingdesk.contracts import spec
from tradingdesk.data.providers.base import HistoricalProvider
from tradingdesk.data.providers.synthetic import SyntheticProvider
from tradingdesk.data.providers.yahoo import YahooProvider, parse_chart
from tradingdesk.data.symbols import active_contracts
from tradingdesk.live.paper import PaperTrader


# ----- Free live feed ---------------------------------------------------------------------------------------
def _chart(stamps, closes, last_trade):
    n = len(stamps)
    return {"chart": {"result": [{"meta": {"regularMarketTime": last_trade}, "timestamp": stamps,
                                  "indicators": {"quote": [{"open": closes, "high": [c + 1 if c else None for c in closes],
                                                            "low": [c - 1 if c else None for c in closes], "close": closes,
                                                            "volume": [10] * n}]}}]}}


def test_yahoo_bars_drop_gaps_and_the_minute_still_forming():
    t0 = int(pd.Timestamp("2026-10-23 14:00", tz="UTC").timestamp())
    df, last = parse_chart(_chart([t0, t0 + 60, t0 + 120, t0 + 180], [100.0, None, 101.0, 102.0], t0 + 200))
    assert list(df["close"]) == [100.0, 101.0]          # None row dropped; 14:03 still forming at 14:03:20
    assert last == pd.Timestamp("2026-10-23 14:03:20", tz="UTC")


def test_yahoo_asks_for_contract_months_then_falls_back():
    asked = []

    class Fake(YahooProvider):
        def _get(self, symbol, start, end):
            asked.append(symbol)
            t = int((end - pd.Timedelta(minutes=30)).timestamp())
            return _chart([t], [5000.0], t + 120) if symbol == "MES=F" else {}

    yp = Fake()
    now = pd.Timestamp.now(tz="UTC")
    bars = yp.fetch_bars("MES", now - pd.Timedelta(hours=3), now)
    assert asked[0].endswith(".CME") and "MES=F" in asked
    assert "MES" in yp.used_fallback and bars["symbol"].iloc[0] == active_contracts("MES", now)[0]


def test_active_contracts_skip_expired_months():
    assert active_contracts("MES", pd.Timestamp("2026-12-19")) == ["MESH27", "MESM27"]   # Dec expired on the 18th
    assert active_contracts("MCL", pd.Timestamp("2026-10-24")) == ["MCLZ26", "MCLF27"]
    assert active_contracts("MGC", pd.Timestamp("2026-10-24"))[0] == "MGCZ26"


# ----- Execution Agent --------------------------------------------------------------------------------------
def test_live_mode_cannot_start_a_broker_yet(settings):
    settings.mode = "live"
    with pytest.raises(LiveTradingNotAvailable):
        make_broker(settings, spec("MES"))


def test_oversized_orders_are_refused(settings):
    from tests.conftest import minute_bars
    from tests.test_trading_rules import FLAT, LONG, Scripted
    from tradingdesk.backtest.engine import Backtester

    settings.execution.max_contracts_per_order = 2
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 30
    res = Backtester(settings, [Scripted(LONG)], {"MES": minute_bars(rows)}).run()
    refused = [d for d in res.decisions if d.action == "order_refused"]
    assert refused and "safety limit" in refused[0].reason
    assert all(f.qty <= 2 for t in res.trades for f in t.fills)


def test_execution_reports_each_note_once(settings):
    logged = []
    ex = ExecutionAgent(settings, lambda *a, **k: logged.append(a))

    class T:
        record = type("R", (), {"id": 1})()
        plan = type("P", (), {"symbol": "MES"})()
        log = [(None, "a", "x")]

    t = T()
    ex.report(t)
    t.log.append((None, "b", "y"))
    ex.report(t)
    assert [a[1] for a in logged] == ["a", "b"]


# ----- Paper trading ----------------------------------------------------------------------------------------
class DelayedFeed(HistoricalProvider):
    """Synthetic prices that only become visible 10 minutes after they happen, like the free feed."""

    def __init__(self, roots, start, end):
        self.bars = {r: SyntheticProvider().fetch_bars(r, start, end) for r in roots}
        self.now = None

    def fetch_bars(self, root, start, end, schema="ohlcv-1m"):
        b = self.bars[root]
        cutoff = min(pd.Timestamp(end), self.now) - pd.Timedelta(minutes=11)
        return b[(b["ts"] >= pd.Timestamp(start)) & (b["ts"] <= cutoff)].copy()


@pytest.fixture
def paper(settings, tmp_path):
    settings.mode = "paper"
    settings.contracts = ["MES", "MGC"]
    settings.paper.state_path = tmp_path / "paper"
    settings.paper.warmup_days = 20
    feed = DelayedFeed(settings.contracts, "2024-02-01", "2024-04-01")
    return settings, feed


def offline(url):
    raise OSError("offline in tests")


def _tick(pt, feed, when):
    feed.now = pd.Timestamp(when)
    feed.now = feed.now.tz_localize("UTC") if feed.now.tz is None else feed.now
    return pt.tick(feed.now)


def test_paper_ticks_are_consistent_with_one_long_replay(paper):
    settings, feed = paper
    pt = PaperTrader(settings, feed, http=offline)
    first = _tick(pt, feed, "2024-03-04 15:00")
    assert first.new == [] and (settings.paper.state_path / "status.md").exists()
    times = pd.date_range("2024-03-04 19:00", "2024-03-15 20:00", freq="10h", tz="UTC")
    new_total = 0
    for t in times:
        r = _tick(pt, feed, t)
        new_total += len(r.new)
        hist = next(c for c in r.checks if c.name == "history")
        assert hist.status == "ok", hist.message   # earlier decisions always replay identically
    assert new_total > 0
    journal = [json.loads(x) for x in (settings.paper.state_path / "journal.jsonl").read_text().splitlines()]
    assert len(journal) == new_total
    assert any(j["action"] == "approved" for j in journal)
    # Running the same update again announces nothing new.
    assert _tick(pt, feed, times[-1]).new == []


def test_kill_switch_stops_new_trades_without_rewriting_history(paper):
    settings, feed = paper
    pt = PaperTrader(settings, feed, http=offline)
    _tick(pt, feed, "2024-03-04 15:00")
    _tick(pt, feed, "2024-03-11 20:00")
    settings.risk.kill_switch = True
    r = _tick(pt, feed, "2024-03-15 20:00")
    assert next(c for c in r.checks if c.name == "history").status == "ok"
    assert not [d for d in r.new if d.action == "approved"]
    assert any("Kill switch" in c.message for c in r.checks)


def test_settings_change_is_reported_as_changed_history(paper):
    settings, feed = paper
    pt = PaperTrader(settings, feed, http=offline)
    _tick(pt, feed, "2024-03-04 15:00")
    first = _tick(pt, feed, "2024-03-12 20:00")
    if not any(d.action == "approved" for d in first.new):
        pytest.skip("no trades in this stretch of made-up data")
    settings.risk.risk_per_trade_pct = 0.5   # every earlier approval now reads differently
    r = _tick(pt, feed, "2024-03-12 21:00")
    assert next(c for c in r.checks if c.name == "history").status == "alert"
    assert "Watchdog" in r.notify_md


# ----- Watchdog ---------------------------------------------------------------------------------------------
def _cont(last):
    idx = pd.date_range(end=last, periods=60, freq="1min", tz="UTC")
    return pd.DataFrame({"close": 1.0}, index=idx)


def test_watchdog_alerts_on_stale_prices_only_while_the_market_is_open(settings):
    wd = Watchdog(settings)
    tue = pd.Timestamp("2024-03-05 16:00", tz="UTC")
    assert wd.data("MES", _cont(tue - pd.Timedelta(minutes=12)), tue)[0].status == "ok"
    assert wd.data("MES", _cont(tue - pd.Timedelta(minutes=90)), tue)[0].status == "alert"
    sat = pd.Timestamp("2024-03-09 16:00", tz="UTC")       # weekend: Friday's close is the newest price
    assert wd.data("MES", _cont(pd.Timestamp("2024-03-08 21:59", tz="UTC")), sat)[0].status == "ok"
    assert open_minutes("MES", pd.Timestamp("2024-03-09", tz="UTC"), pd.Timestamp("2024-03-10", tz="UTC")) == 0


def test_watchdog_flags_a_position_without_a_stop(settings):
    from tests.test_phase2_agents import FakeTrade

    t = FakeTrade("MES")
    t.record = type("R", (), {"id": 7})()
    t.working = lambda purpose: None
    t.breakeven_done, t.risk_used = False, lambda s: 0.0
    checks = Watchdog(settings).positions(type("E", (), {"open_trades": [t]})())
    assert checks[0].status == "alert" and "without a full working stop" in checks[0].message
