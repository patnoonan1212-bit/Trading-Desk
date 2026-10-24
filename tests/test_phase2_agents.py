"""Regime Agent, Scanner, Risk Manager, economic calendar and walk-forward helpers."""

import numpy as np
import pandas as pd
import pytest

from tradingdesk.agents.regime import RegimeAgent
from tradingdesk.agents.risk import RiskManager
from tradingdesk.agents.scanner import ScannerAgent, _Row
from tradingdesk.backtest.broker_sim import Bar
from tradingdesk.backtest.engine import Backtester
from tradingdesk.backtest.models import TradePlan, TradeRecord
from tradingdesk.backtest.walkforward import make_windows, t_stat
from tradingdesk.calendar import EconomicCalendar, build_default_calendar


# ----- Regime Agent --------------------------------------------------------------------------------------
def _hourly(closes, start="2024-03-04 14:00"):
    idx = pd.date_range(start, periods=len(closes), freq="1h", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": 100.0, "end": idx + pd.Timedelta(hours=1)}, index=idx)


def test_regime_reads_trends_and_ranges(settings):
    ra = RegimeAgent(settings.regime)
    up = ra.classify(_hourly(5000 + np.arange(300) * 2.0))
    assert up["trend"].iloc[-1] == "trending_up"
    down = ra.classify(_hourly(5000 - np.arange(300) * 2.0))
    assert down["trend"].iloc[-1] == "trending_down"
    chop = ra.classify(_hourly(5000 + np.tile([0, 3, 0, -3], 75)))
    assert chop["trend"].iloc[-1] == "ranging"


def test_regime_is_never_taken_from_the_future(settings):
    ra = RegimeAgent(settings.regime)
    hourly = ra.classify(_hourly(5000 + np.arange(300) * 2.0))
    idx = pd.date_range("2024-03-10 00:00", periods=40, freq="15min", tz="UTC")
    setup = pd.DataFrame({"close": 1.0, "end": idx + pd.Timedelta(minutes=15)}, index=idx)
    aligned = RegimeAgent.align(hourly, setup)
    ends = hourly["end"]
    for _ts, row in aligned.iterrows():
        used = ends[ends <= row["end"]]
        assert len(used)  # a reading that had already finished
    # A 15m bar ending at 00:15 must use the hour that ended at 00:00, not the one ending at 01:00.
    first = aligned.iloc[0]
    expected = hourly.loc[hourly["end"] <= first["end"], "trend"].iloc[-1]
    assert first["trend"] == expected


def test_regime_filter_follows_the_settings(settings):
    ra = RegimeAgent(settings.regime)
    assert not ra.allows("mean_reversion", "trending_up", "normal")[0]
    assert ra.allows("mean_reversion", "ranging", "normal")[0]
    assert not ra.allows("mean_reversion", "ranging", "high")[0]
    assert not ra.allows("pullback", "ranging", "normal")[0]
    assert "choppy" in ra.allows("pullback", "ranging", "normal")[1]


# ----- Scanner --------------------------------------------------------------------------------------------
def _row(**vals):
    return _Row({k: np.array([v]) for k, v in vals.items()}, 0)


def test_breakout_needs_volume_and_has_structural_stop_and_target(settings):
    sc = ScannerAgent(settings)
    p = settings.scanner.setups["trend_breakout"]
    base = dict(open=5000, high=5012, low=4999, close=5010, atr=4.0, vol_ratio=2.0, tb_hh=5005, tb_ll=4980, tb_low5=4995, tb_high5=5012)
    c = sc._trend_breakout(_row(**base), None, 4.0, p, "trending_up")
    assert c.direction == 1 and c.confirm_level == 5005
    assert c.stop == pytest.approx(4995 - 0.25 * 4)
    assert c.target == pytest.approx(5010 + max(6 * 4, 25))
    assert c.reward_risk > 1
    assert sc._trend_breakout(_row(**{**base, "vol_ratio": 1.0}), None, 4.0, p, "trending_up") is None   # no volume
    assert sc._trend_breakout(_row(**base), None, 4.0, p, "trending_down") is None                     # against trend


def test_liquidity_sweep_long(settings):
    sc = ScannerAgent(settings)
    p = settings.scanner.setups["liquidity_sweep"]
    r = _row(open=4990, high=4996, low=4988, close=4995, ls_hh=5030, ls_ll=4990)
    c = sc._liquidity_sweep(r, None, 4.0, p, "ranging")
    assert c.direction == 1 and c.target == 5030 and c.stop == pytest.approx(4988 - 1.0)
    assert c.reward_risk == pytest.approx((5030 - 4995) / (4995 - 4987))


def test_scores_reward_trading_with_the_trend(settings):
    from tradingdesk.agents.scanner import Candidate

    sc = ScannerAgent(settings)
    r = _row(vol_ratio=1.5, end=pd.Timestamp("2024-03-05 16:00", tz="UTC"))
    long = Candidate("x", 1, 100, 99, 104, 0, "", None)
    short = Candidate("x", -1, 100, 101, 96, 0, "", None)
    assert sc._score(long, r, "trending_up", "normal") > sc._score(short, r, "trending_up", "normal")


# ----- Risk Manager ---------------------------------------------------------------------------------------
class FakeTrade:
    def __init__(self, symbol, state="full", position=3, avg_entry=5000.0, stop=4990.0, side=1, pv=5.0, budget=750.0):
        self.plan = TradePlan(symbol, side, avg_entry, stop, avg_entry + 30, "t", "t")
        self.state, self.position, self.avg_entry, self.stop, self.side, self.pv, self.budget = state, position, avg_entry, stop, side, pv, budget


class FakeEngine:
    def __init__(self, equity=50000.0, trades=()):
        self._equity, self.open_trades, self.decisions = equity, list(trades), []

    def equity(self):
        return self._equity

    def decide(self, agent, action, reason, symbol, ts, **kw):
        self.decisions.append((action, reason))


def _b(ts="2024-03-05 15:00"):
    return Bar(1, pd.Timestamp(ts, tz="UTC"), 5000, 5000, 5000, 5000, 500, 0.0, "us_regular", "MESH4")


PLAN = TradePlan("MES", 1, 5000, 4990, 5030, "t", "t")


@pytest.fixture
def rm(settings):
    return RiskManager(settings, EconomicCalendar(build_default_calendar(2024, 2024)))


def test_daily_loss_stops_new_trades_for_the_day(rm):
    eng = FakeEngine(50000)
    assert rm.check(PLAN, eng, _b()).allowed
    eng._equity = 47900  # down 4.2% today
    v = rm.check(PLAN, eng, _b("2024-03-05 16:00"))
    assert not v.allowed and "Daily limit" in v.reason
    assert any(a == "alert" for a, _ in eng.decisions)
    eng._equity = 49000  # even after a bounce, still done for the day
    assert not rm.check(PLAN, eng, _b("2024-03-05 18:00")).allowed
    assert rm.check(PLAN, eng, _b("2024-03-06 15:00")).allowed  # next trading day


def test_losing_streak(rm):
    eng = FakeEngine()
    rm.check(PLAN, eng, _b())
    for _ in range(3):
        rec = TradeRecord(1, "MES", "t", 1, "t", pd.Timestamp("2024-03-05", tz="UTC"), "us_regular", 4990, 5030)
        rec.fills, rec.gross_pnl = [object()], -100
        rm.on_trade_closed(rec, eng)
    v = rm.check(PLAN, eng, _b("2024-03-05 16:00"))
    assert not v.allowed and "Losing streak" in v.reason


def test_weekly_drawdown_pauses_until_next_week(rm):
    eng = FakeEngine(50000)
    rm.check(PLAN, eng, _b("2024-03-04 15:00"))  # Monday
    eng._equity = 45900                          # -8.2% on the week
    v = rm.check(PLAN, eng, _b("2024-03-06 15:00"))
    assert not v.allowed and "Weekly limit" in v.reason
    assert rm.check(PLAN, eng, _b("2024-03-11 15:00")).allowed  # next Monday


def test_correlated_contracts_share_a_budget(rm):
    open_mnq = FakeTrade("MNQ", position=40, avg_entry=18000, stop=17990, pv=2.0)   # $800 at risk
    v = rm.check(PLAN, FakeEngine(50000, [open_mnq]), _b())
    assert v.allowed and v.max_budget == pytest.approx(1000 - 800 + 0, abs=1)  # group limit 2% = $1,000
    heavy = FakeTrade("MNQ", position=48, avg_entry=18000, stop=17990, pv=2.0)     # $960 at risk
    v = rm.check(PLAN, FakeEngine(50000, [heavy]), _b())
    assert not v.allowed and "Correlated exposure" in v.reason
    gold = TradePlan("MGC", 1, 2000, 1995, 2020, "t", "t")
    assert rm.check(gold, FakeEngine(50000, [heavy]), _b()).max_budget is None      # gold isn't in the group


def test_news_blackout_around_jobs_report(rm):
    nfp = "2024-03-08 13:30"  # 08:30 ET on the first Friday of March 2024
    v = rm.check(PLAN, FakeEngine(), _b("2024-03-08 13:25"))
    assert not v.allowed and "jobs report" in v.reason
    assert not rm.check(PLAN, FakeEngine(), _b(nfp)).allowed
    assert rm.check(PLAN, FakeEngine(), _b("2024-03-08 13:45")).allowed


def test_kill_switch_and_circuit_breaker(settings):
    settings.risk.kill_switch = True
    assert not RiskManager(settings, EconomicCalendar(build_default_calendar(2024, 2024))).check(PLAN, FakeEngine(), _b()).allowed
    settings.risk.kill_switch = False
    rm = RiskManager(settings, EconomicCalendar(build_default_calendar(2024, 2024)))
    eng = FakeEngine(60000)
    rm.check(PLAN, eng, _b("2024-03-04 15:00"))
    eng._equity = 47000  # 21.7% below the 60k peak, spread over weeks so the weekly limit isn't what stops it
    rm.week.start_equity = rm.day.start_equity = 47000
    v = rm.check(PLAN, eng, _b("2024-03-04 16:00"))
    assert not v.allowed and "Circuit breaker" in v.reason
    eng._equity = 70000
    assert not rm.check(PLAN, eng, _b("2024-04-04 16:00")).allowed  # never resumes by itself


def test_calendar_has_first_friday_jobs_reports_and_fed_days():
    cal = build_default_calendar(2024, 2024)
    nfp = cal[cal["event"].str.contains("NFP")]["time_et"].tolist()
    # Real release dates: the rule matches the Labor Department's schedule, which isn't always the first Friday.
    for d in ("2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05", "2024-05-03", "2024-06-07", "2024-08-02", "2024-09-06", "2024-12-06"):
        assert f"{d} 08:30" in nfp, d
    assert len(nfp) == 12
    assert "2024-09-18 14:00" in cal["time_et"].tolist()


# ----- Engine and walk-forward helpers --------------------------------------------------------------------
def test_engine_does_not_trade_during_warmup(settings):
    from tests.conftest import minute_bars
    from tests.test_trading_rules import FLAT, LONG, Scripted

    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 5
    res = Backtester(settings, [Scripted(LONG)], {"MES": minute_bars(rows)}, trade_from="2024-03-05 15:00").run()
    assert res.trades == []


def test_walkforward_windows_and_t_stat():
    w = make_windows("2023-01-01", "2024-01-01", 6, 3)
    assert len(w) == 2
    assert w[0].test_start == w[0].train_end and w[1].train_start == pd.Timestamp("2023-04-01", tz="UTC")
    assert all(x.test_end <= pd.Timestamp("2024-01-01", tz="UTC") for x in w)
    assert t_stat(pd.Series([1.0, 1.0, 1.0])) == 0.0
    assert t_stat(pd.Series([1.0, 2.0, 3.0])) == pytest.approx(2 / 1 * np.sqrt(3))
