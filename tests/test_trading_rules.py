"""The entry, exit and fill rules, checked on hand-made price action where the right answer is known."""

import pandas as pd
import pytest

from tests.conftest import minute_bars
from tradingdesk.backtest.broker_sim import Bar, SimBroker
from tradingdesk.backtest.engine import Backtester
from tradingdesk.backtest.models import Order, TradePlan, TradeRecord
from tradingdesk.backtest.strategy import Strategy
from tradingdesk.backtest.trade import Trade
from tradingdesk.contracts import spec

FLAT = (5000, 5000, 5000, 5000)


class Scripted(Strategy):
    """Proposes one fixed plan when setup bar `at` closes, and records when it was asked."""

    name = "scripted"
    timeframe = "5m"

    def __init__(self, plan_kwargs, at=0):
        self.plan_kwargs, self.at, self.calls = plan_kwargs, at, []

    def on_bar(self, symbol, bars, i, ctx):
        self.calls.append((i, bars.index[i]))
        if i == self.at:
            return [TradePlan(symbol, setup="scripted", reason="test", timeframe="5m", **self.plan_kwargs)]
        return []


LONG = dict(direction=1, entry=5000.0, stop=4990.0, target=5030.0, confirm_level=5000.0)


def run(settings, rows, plan=LONG, equity=None):
    if equity:
        settings.account.starting_equity = equity
    strat = Scripted(plan)
    bt = Backtester(settings, [strat], {"MES": minute_bars(rows)})
    res = bt.run()
    return res, bt, strat


def test_setup_bar_is_seen_only_after_it_completes(settings):
    _, bt, strat = run(settings, [FLAT] * 12, plan=dict(LONG, target=5000.5))  # target too close: no trade
    first_call_index, first_bar_ts = strat.calls[0]
    assert first_call_index == 0 and first_bar_ts == pd.Timestamp("2024-03-05 14:30", tz="UTC")
    # The 5-minute bar starting 14:30 must be delivered on its last minute (14:34), not earlier or at the end.
    approved = [d for d in bt.bus.history if getattr(d, "action", "") == "rejected"]
    assert approved and approved[0].ts == pd.Timestamp("2024-03-05 14:34", tz="UTC")


def test_scale_in_respects_total_risk_and_stop_beats_target_in_same_minute(settings):
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001), (5001, 5040, 4985, 5000)] + [FLAT] * 3
    res, bt, _ = run(settings, rows)
    t = res.trades[0]
    assert t.mode == "thirds"
    entries = [f for f in t.fills if f.purpose in ("entry", "add")]
    assert [f.purpose for f in entries] == ["entry", "add"]
    assert entries[0].qty == 4 and entries[0].price == 5000.25          # starter: next open plus 1 tick slippage
    assert not any(f.purpose == "partial" for f in t.fills)             # target and stop in one minute: stop first
    assert t.exit_reasons == ["stopped out"]
    assert -t.net_pnl <= t.risk_dollars                                 # whole position never risked more than 1.5%
    assert t.risk_dollars == pytest.approx(750)


def test_partial_profit_breakeven_then_runner(settings):
    up = [(5001 + i * 3, 5004 + i * 3, 5000 + i * 3, 5003 + i * 3) for i in range(12)]  # steady climb past 2R
    back = [(5030, 5030, 5000, 5000)] * 2                                               # falls back to entry
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] + up + back
    res, bt, _ = run(settings, rows)
    t = res.trades[0]
    purposes = [f.purpose for f in t.fills]
    assert "partial" in purposes
    partial = next(f for f in t.fills if f.purpose == "partial")
    assert partial.qty == round(t.max_contracts * 0.3333)
    stop_fill = next(f for f in t.fills if f.purpose == "stop")
    entry_avg = sum(f.price * f.qty for f in t.fills if f.purpose in ("entry", "add")) / sum(f.qty for f in t.fills if f.purpose in ("entry", "add"))
    assert stop_fill.price >= entry_avg - 0.5                            # breakeven stop (minus slippage)
    assert t.net_pnl > 0                                                 # partial profit locked in
    assert any(d.action == "breakeven" for d in bt.bus.history if hasattr(d, "action"))


def test_starter_is_closed_if_confirmation_never_comes(settings):
    rows = [FLAT] * 5 + [(5000, 5000, 4998, 4999)] * 14
    res, _, _ = run(settings, rows)
    t = res.trades[0]
    assert "no confirmation" in t.exit_reasons[0]
    assert [f.purpose for f in t.fills] == ["entry", "exit"]


def test_small_account_enters_once_after_confirmation(settings):
    plan = dict(direction=1, entry=5000.0, stop=4996.0, target=5012.0, confirm_level=5000.0)  # still fits $30 after confirming at 5001
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001), (5001, 5001, 5001, 5001)] + [FLAT] * 3
    res, _, _ = run(settings, rows, plan=plan, equity=2000)
    t = res.trades[0]
    assert t.mode == "single"
    entries = [f for f in t.fills if f.purpose in ("entry", "add")]
    assert len(entries) == 1 and entries[0].qty == 1
    assert entries[0].ts == pd.Timestamp("2024-03-05 14:36", tz="UTC")  # after the confirming 14:35 close


def test_trade_too_big_for_the_account_is_rejected(settings):
    res, bt, _ = run(settings, [FLAT] * 12, equity=2000)                 # 10-point stop is $54 a contract > $30
    assert res.trades == []
    assert any("even 1 contract" in d.reason for d in bt.bus.history if getattr(d, "action", "") == "rejected")


def test_reward_to_risk_below_minimum_is_rejected(settings):
    res, bt, _ = run(settings, [FLAT] * 12, plan=dict(LONG, target=5020.0))  # 2:1
    assert res.trades == []
    assert any("below the 3:1 minimum" in d.reason for d in bt.bus.history if getattr(d, "action", "") == "rejected")


def test_equity_matches_the_sum_of_trades(settings):
    up = [(5001 + i * 3, 5004 + i * 3, 5000 + i * 3, 5003 + i * 3) for i in range(12)]
    rows = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] + up + [(5030, 5030, 5000, 5000)] * 2
    res, bt, _ = run(settings, rows)
    assert res.equity.iloc[-1] == pytest.approx(50000 + sum(t.net_pnl for t in res.trades), abs=0.01)


# ----- broker fill rules ---------------------------------------------------------------------------------
def _bar(seq, o, h, lo, c, session="us_regular", volume=1000):
    return Bar(seq, pd.Timestamp("2024-03-05 15:00", tz="UTC"), o, h, lo, c, volume, 0.0, session, "MESH4")


@pytest.fixture
def broker(settings):
    return SimBroker(spec("MES"), 0.85, settings.costs.slippage_ticks)


def test_orders_never_fill_on_the_bar_that_created_them(broker):
    o = Order("MES", 1, 1, "market", active_from=5)
    assert broker.try_fill(o, _bar(4, 100, 101, 99, 100)) is None
    f = broker.try_fill(o, _bar(5, 100, 101, 99, 100))
    assert f.price == 100.25 and f.intended == 100


def test_stop_gapped_through_fills_at_the_open(broker):
    o = Order("MES", -1, 1, "stop", 4990)
    f = broker.try_fill(o, _bar(1, 4980, 4982, 4975, 4978))
    assert f.price == 4979.75  # open 4980 minus 1 tick of slippage, not the 4990 stop


def test_limit_needs_price_to_trade_through(broker):
    o = Order("MES", -1, 2, "limit", 5020)
    assert broker.try_fill(o, _bar(1, 5010, 5020, 5005, 5015)) is None   # only touched
    f = broker.try_fill(o, _bar(2, 5010, 5020.25, 5005, 5015))
    assert f.price == 5020 and f.slippage_points == 0


def test_overnight_slippage_is_larger(broker):
    o = Order("MES", 1, 1, "market")
    f = broker.try_fill(o, _bar(1, 100, 101, 99, 100, session="overnight"))
    assert f.price == 100.5


def test_thin_market_fills_partially(broker):
    o = Order("MES", 1, 10, "market")
    f = broker.try_fill(o, _bar(1, 100, 101, 99, 100, volume=8))
    assert f.qty == 2 and o.remaining == 8


def test_trailing_stop_only_moves_in_our_favour(settings, broker):
    plan = TradePlan("MES", 1, 5000, 4990, 5030, "t", "t")
    t = Trade(TradeRecord(1, "MES", "t", 1, "t", pd.Timestamp("2024-03-05", tz="UTC"), "us_regular", 4990, 5030), plan, broker, settings.execution, 750, "single")
    t.position, t.avg_entry, t.breakeven_done, t.stop = 3, 5000, True, 5000
    b = _bar(10, 5040, 5040, 5040, 5040)
    t.on_setup_bar(5040, 5.0, b)            # 5040 - 3 x 5 = 5025
    assert t.stop == 5025
    t.on_setup_bar(5030, 5.0, b)            # would be 5015: must not loosen
    assert t.stop == 5025
