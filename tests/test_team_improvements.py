"""Drawdown brake, weekend close, stale-trade exit, score-based sizing, the head trader's memory and the
four level-based setups."""

import numpy as np
import pandas as pd
import pytest

from tests.conftest import minute_bars
from tests.test_trading_rules import FLAT, LONG, Scripted
from tradingdesk.agents.scanner import ScannerAgent, _Row, session_levels
from tradingdesk.backtest.engine import Backtester

ROWS = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 30


def _run(settings, rows=ROWS, start="2024-03-05 14:30", plan=LONG, **kw):
    return Backtester(settings, [Scripted(plan)], {"MES": minute_bars(rows, start=start)}, **kw).run()


# ----- capital protection ---------------------------------------------------------------------------------
def test_drawdown_brake_halves_risk_below_the_high(settings):
    full = _run(settings).trades[0]
    settings.account.starting_equity = 50000
    eng = Backtester(settings, [Scripted(LONG)], {"MES": minute_bars(ROWS)})
    eng.peak_equity = 60000                      # the account is ~17% below its high
    res = eng.run()
    braked = res.trades[0]
    assert braked.risk_dollars == pytest.approx(full.risk_dollars / 2, rel=0.01)
    assert any(d.action == "brake" and "Drawdown brake" in d.reason for d in res.decisions)
    settings.risk.drawdown_brake.start_pct = 0  # off
    eng = Backtester(settings, [Scripted(LONG)], {"MES": minute_bars(ROWS)})
    eng.peak_equity = 60000
    assert eng.run().trades[0].risk_dollars == pytest.approx(full.risk_dollars, rel=0.01)


def test_weekend_close_and_no_new_entries_late_friday(settings):
    # Friday 2024-03-08: 16:20 ET = 21:20 UTC. The trade opens, then is closed at 16:30 ET.
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 40
    res = _run(settings, rows, start="2024-03-08 21:00")
    assert res.trades and "weekend" in res.trades[0].exit_reasons[-1]
    late = _run(settings, rows, start="2024-03-08 21:31")   # 16:31 ET: too late to open anything
    assert late.trades == [] and any("weekend" in d.reason for d in late.decisions if d.action == "rejected")
    settings.execution.weekend.flatten = False
    assert "weekend" not in " ".join(_run(settings, rows, start="2024-03-08 21:00").trades[0].exit_reasons)


def test_stale_trades_are_closed_only_when_switched_on(settings):
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 3 + [(5001, 5002, 5000, 5001)] * 300
    assert "nowhere" not in " ".join(_run(settings, rows).trades[0].exit_reasons)
    settings.execution.stale_exit.hours = 2
    t = _run(settings, rows).trades[0]
    assert "went nowhere for 2 hours" in t.exit_reasons[-1]


def test_score_sizing_never_exceeds_the_cap(settings):
    ss = settings.execution.score_sizing
    assert ss.pct_for(55, 1.5) == 1.5                        # off: everyone risks the normal amount
    ss.enabled = True
    assert ss.pct_for(55, 1.5) == 0.75 and ss.pct_for(70, 1.5) == 1.1 and ss.pct_for(95, 1.5) == 1.5
    assert ss.pct_for(95, 1.0) == 1.0                        # the cap always wins
    plan = {**LONG, "context": {"score": 55.0}}
    full = _run(settings).trades[0].risk_dollars
    res = _run(settings, plan=plan)
    assert res.trades[0].risk_dollars == pytest.approx(full / 2, rel=0.01)
    assert any(d.action == "sized" for d in res.decisions)


# ----- session levels and the new setups ------------------------------------------------------------------
def _day(open_px=5000.0):
    """15-minute MES bars from 18:00 ET the evening before to 16:00 ET on Tue 2024-03-05."""
    idx = pd.date_range("2024-03-04 23:00", "2024-03-05 21:00", freq="15min", tz="UTC", inclusive="left")
    n = len(idx)
    c = open_px + np.sin(np.arange(n) / 5) * 4
    b = pd.DataFrame({"open": c, "high": c + 2, "low": c - 2, "close": c, "volume": 100.0}, index=idx)
    b["end"] = idx + pd.Timedelta(minutes=15)
    return b


def test_session_levels_only_use_finished_bars():
    b = _day()
    lv = session_levels(b, "MES")
    et = b.index.tz_convert("America/New_York")
    before_or_done = (et.hour * 60 + et.minute) < 10 * 60
    assert lv.loc[before_or_done, "or_high"].isna().all()                # unknown until 10:00 ET
    after = lv[~before_or_done & (et.hour < 17)]
    in_or = b[(et.hour * 60 + et.minute >= 570) & (et.hour * 60 + et.minute < 600)]
    assert after["or_high"].iloc[0] == in_or["high"].max()
    pre = b[(et.hour * 60 + et.minute < 570) | (et.hour >= 18)]          # overnight = 6 pm the evening before to the open
    assert after["on_high"].iloc[0] == pre["high"].max()
    assert lv["pd_high"].isna().all()                                     # no previous day in this frame
    assert lv.loc[(et.hour * 60 + et.minute) < 570, "vwap"].isna().all()


def _row(**v):
    return _Row({k: np.array([x]) for k, x in v.items()}, 0)


def test_opening_range_breakout(settings):
    sc = ScannerAgent(settings)
    p = settings.scanner.setups["opening_range"]
    r = _row(open=5010, high=5016, low=5009, close=5015, vol_ratio=1.6, or_high=5012, or_low=5000, mins_after_open=45)
    prev = _row(close=5011)
    c = sc._opening_range(r, prev, 4.0, p, "transition")
    assert c.direction == 1 and c.stop == pytest.approx(5006 - 1.0) and c.target == pytest.approx(5015 + 24)
    assert sc._opening_range(_row(**{**{k: r[k] for k in r._c}, "mins_after_open": 200}), prev, 4.0, p, "transition") is None


def test_overnight_rejection_and_vwap_reclaim(settings):
    sc = ScannerAgent(settings)
    r = _row(open=5021, high=5022, low=5014, close=5015, on_high=5020, on_low=4980, mins_after_open=30)
    c = sc._overnight_reject(r, None, 4.0, settings.scanner.setups["overnight_reject"], "ranging")
    assert c.direction == -1 and c.target == 4980
    r = _row(open=4998, high=5004, low=4996, close=5003, vwap=5001, sw_low4=4994, sw_high4=5010, day_high=5030, pd_high=5040, on_high=5025)
    c = sc._vwap_reclaim(r, _row(close=4999, vwap=5001), 4.0, settings.scanner.setups["vwap_reclaim"], "trending_up")
    assert c.direction == 1 and c.target == 5040 and c.stop == pytest.approx(4993)
    assert sc._vwap_reclaim(r, _row(close=4999, vwap=5001), 4.0, settings.scanner.setups["vwap_reclaim"], "ranging") is None


def test_new_setups_run_in_a_full_backtest(settings):
    from tradingdesk.data.providers.synthetic import SyntheticProvider
    from tradingdesk.data.rollover import build_continuous

    data = {"MES": build_continuous(SyntheticProvider().fetch_bars("MES", "2024-01-01", "2024-03-01"))}
    sc = ScannerAgent(settings, {k: v for k, v in settings.scanner.enabled().items() if k in
                                 ("opening_range", "prior_day_level", "overnight_reject", "vwap_reclaim")})
    res = Backtester(settings, [sc], data).run()
    seen = {d.reason.split(":")[0] for d in res.decisions if d.agent == "scanner"} | {t.setup for t in res.trades}
    assert len(seen & {"opening range", "prior day level", "overnight reject", "vwap reclaim",
                       "opening_range", "prior_day_level", "overnight_reject", "vwap_reclaim"}) >= 2


# ----- the head trader's memory ------------------------------------------------------------------------
def test_head_trader_sees_learning_findings_and_recent_record(settings, tmp_path):
    import json

    from tests.test_phase4_reasoning_news import claude
    from tradingdesk.agents.reasoning import Overseer

    lf = tmp_path / "latest.json"
    lf.write_text(json.dumps({"generated": "2024-03-02T14:00:00", "trades": 400, "groups": [
        {"area": "Setup", "name": "scripted", "trades": 60, "exp_r": -0.3, "verdict": "clearly negative"},
        {"area": "Setup", "name": "pullback", "trades": 60, "exp_r": 0.4, "verdict": "clearly positive"}]}))
    settings.paper.learning_file = lf
    c = claude([{"decision": "approve", "size_factor": 1, "reason": "ok"}])
    _run(settings, reviewer=Overseer(settings, c))
    brief = c.client().calls[0]["messages"][0]["content"]
    assert "## Desk memory" in brief and "scripted, 60 trades, average -0.30R (clearly negative)" in brief
    assert "pullback" not in brief.split("## Desk memory")[1]       # only what bears on this idea


def test_learning_agent_suggests_score_sizing_only_when_scores_matter(settings):
    from tests.test_phase5_learning_dashboard import _trades
    from tradingdesk.agents.learning import enrich, propose

    weak = _trades(40, "pullback", -0.1, score=55.0)
    strong = _trades(40, "trend_breakout", 0.5, score=85.0)
    titles = [p.title for p in propose(enrich(pd.concat([weak, strong], ignore_index=True)), settings, 30)]
    assert "Risk less on lower-scoring setups" in titles
    same = _trades(40, "trend_breakout", 0.3, score=85.0)
    flat = _trades(40, "pullback", 0.3, score=55.0)
    titles = [p.title for p in propose(enrich(pd.concat([flat, same], ignore_index=True)), settings, 30)]
    assert "Risk less on lower-scoring setups" not in titles
