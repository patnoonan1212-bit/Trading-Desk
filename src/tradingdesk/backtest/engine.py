"""Event-driven backtester.

It replays history one 1-minute bar at a time, across all contracts in time order, and runs exactly the steps
the live system will run:

  for each 1-minute bar:
    1. charge the cost of rolling any open position if the contract just rolled
    2. let the simulated broker fill working orders (entries, adds, stops, partial profits)
    3. let each open trade react to the bar's close (confirmation, timeouts, breakeven)
    4. after-trade bookkeeping (re-entry watch, "stopped out then target reached" watch)
    5. if a setup-timeframe bar (e.g. 15m) just completed: trail stops, ask strategies for ideas,
       check, size and start new trades
    6. record equity

Strategies only ever see completed bars, and orders created on a bar can only fill from the next bar, so the
backtest never trades on information it wouldn't have had.

The engine applies the per-trade checks (reward-to-risk, position size, open-position count, liquidity and the
daily break); the Risk Manager plugs in at the `RiskGate` step; orders and fills go through the Execution Agent.

The same engine runs paper trading: there it is fed live bars instead of history (see live/paper.py), and with
`close_at_end=False` it leaves open trades open when the data runs out.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingdesk.agents.execution import Broker, ExecutionAgent
from tradingdesk.backtest.broker_sim import Bar
from tradingdesk.backtest.models import TradePlan, TradeRecord
from tradingdesk.backtest.strategy import Strategy
from tradingdesk.backtest.trade import Trade
from tradingdesk.config import Settings
from tradingdesk.contracts import spec
from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import Decision
from tradingdesk.data.resample import resample
from tradingdesk.indicators import atr
from tradingdesk.timeutil import ET, in_window, sessions_of

log = logging.getLogger(__name__)

_FIFTEEN_MIN = 15 * 60 * 10**9
WATCH_BARS_AFTER_STOP = 1380  # about one trading day of 1-minute bars
REENTRY_WINDOW_BARS = 60


def _ns(index: pd.DatetimeIndex) -> np.ndarray:
    """Timestamps as int64 nanoseconds, whatever resolution pandas chose to store them in."""
    return index.as_unit("ns").asi8


@dataclass
class Verdict:
    """The risk check's answer: allowed or not, why, and optionally a smaller dollar budget than usual."""

    allowed: bool
    reason: str = ""
    max_budget: float | None = None


class RiskGate:
    """Hook for portfolio-level risk rules. The default allows everything; the Risk Manager Agent replaces it."""

    def check(self, plan: TradePlan, engine: Backtester, bar: Bar) -> Verdict:
        return Verdict(True)

    def on_trade_closed(self, record: TradeRecord, engine: Backtester) -> None:
        pass


@dataclass
class BacktestResult:
    settings: Settings
    trades: list[TradeRecord]
    equity: pd.Series
    decisions: list[Decision]
    symbols: list[str]
    strategies: list[str]
    start: pd.Timestamp | None
    end: pd.Timestamp | None


@dataclass
class _Feed:
    root: str
    ts: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    v: np.ndarray
    adj: np.ndarray
    roll: np.ndarray
    contract: np.ndarray
    session: np.ndarray
    broker: Broker
    setups: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # 1m index -> [(strategy, setup bar)]

    ts_ns: np.ndarray | None = None

    def bar(self, i: int) -> Bar:
        if self.ts_ns is None:
            self.ts_ns = _ns(self.ts)
        return Bar(i, int(self.ts_ns[i]), self.o[i], self.h[i], self.lo[i], self.c[i], self.v[i], self.adj[i], self.session[i], self.contract[i])


class Backtester:
    def __init__(self, settings: Settings, strategies: list[Strategy], data: dict[str, pd.DataFrame], bus: EventBus | None = None,
                 risk_gate: RiskGate | None = None, trade_from=None, close_at_end: bool = True, reviewer=None) -> None:
        self.s = settings
        self.strategies = strategies
        self.data = data
        self.bus = bus or EventBus()
        self.risk_gate = risk_gate or RiskGate()
        self.execution = ExecutionAgent(settings, self.decide)
        self.close_at_end = close_at_end
        self.reviewer = reviewer  # head trader (paper/live only): reviews ideas the rules have already approved
        # Sudden-move guard: typical 1-minute range per contract, and when a pause after an outsized bar ends.
        self._range_avg: dict[str, float] = {}
        self._range_n: dict[str, int] = defaultdict(int)
        self.shock_until_ns: dict[str, int] = {}
        self.peak_equity = settings.account.starting_equity
        # Bars before this time only warm up indicators; no trades are opened on them.
        self.trade_from_ns = None if trade_from is None else pd.Timestamp(trade_from).as_unit("ns").value
        self.realised = 0.0
        self.trades: list[Trade] = []
        self.open: dict[str, list[Trade]] = defaultdict(list)
        self.last_close: dict[str, float] = {}
        self.setup_bars: dict[tuple[str, int], pd.DataFrame] = {}
        self.last_atr: dict[tuple[str, str], float] = {}
        self._watch_target: list[tuple[Trade, int, str]] = []
        self._watch_reentry: list[dict] = []

    # ----- context used by strategies ------------------------------------------------------------------
    def has_trade(self, symbol: str) -> bool:
        return any(t.is_open for t in self.open[symbol]) or any(w["plan"].symbol == symbol for w in self._watch_reentry)

    @property
    def open_trades(self) -> list[Trade]:
        return [t for ts in self.open.values() for t in ts if t.is_open]

    def equity(self) -> float:
        unreal = sum(
            (self.last_close.get(t.plan.symbol, t.avg_entry) - t.avg_entry) * t.side * t.position * t.pv for t in self.open_trades if t.position
        )
        return self.s.account.starting_equity + self.realised + unreal

    def decide(self, agent: str, action: str, reason: str, symbol: str | None, ts, **details) -> None:
        self.bus.publish(Decision(agent=agent, action=action, reason=reason, symbol=symbol, ts=ts, details=details))

    # ----- setup ---------------------------------------------------------------------------------------
    def _prepare(self) -> list[_Feed]:
        feeds = []
        for root, cont in self.data.items():
            if cont.empty:
                continue
            sp = spec(root)
            f = _Feed(
                root=root, ts=cont.index, o=cont["open"].to_numpy(float), h=cont["high"].to_numpy(float),
                lo=cont["low"].to_numpy(float), c=cont["close"].to_numpy(float), v=cont["volume"].to_numpy(float),
                adj=cont["adjustment"].to_numpy(float), roll=cont["roll"].to_numpy(bool), contract=cont["contract"].to_numpy(str),
                session=sessions_of(cont.index).to_numpy(str), broker=self.execution.broker_for(sp),
            )
            for si, strat in enumerate(self.strategies):
                htf = strat.prepare(root, resample(cont, strat.timeframe))
                htf["trail_atr"] = atr(htf, self.s.execution.exits.trail.atr_period)
                self.setup_bars[(root, si)] = htf
                # The 1-minute bar on which each setup bar completes (its last minute).
                # Compare both in nanoseconds: pandas may store times at different resolutions.
                ends = _ns(pd.DatetimeIndex(htf["end"]))
                done_at = np.searchsorted(_ns(cont.index), ends, side="left") - 1
                # A setup bar whose time isn't over when the data stops is incomplete (live data still arriving): skip it.
                complete = ends <= _ns(cont.index[-1:])[0] + 60 * 10**9
                for k, i in enumerate(done_at):
                    if i >= 0 and complete[k]:
                        f.setups.setdefault(int(i), []).append((si, k))
            feeds.append(f)
        return feeds

    # ----- main loop -----------------------------------------------------------------------------------
    def run(self) -> BacktestResult:
        feeds = self._prepare()
        if not feeds:
            raise ValueError("No data to backtest.")
        keys = np.concatenate([_ns(f.ts) for f in feeds])
        which = np.concatenate([np.full(len(f.ts), n) for n, f in enumerate(feeds)])
        idx = np.concatenate([np.arange(len(f.ts)) for f in feeds])
        order = np.argsort(keys, kind="stable")
        equity_points: list[tuple[pd.Timestamp, float]] = []
        last_snap = None

        for k in order:
            f = feeds[which[k]]
            i = int(idx[k])
            bar = f.bar(i)
            self.last_close[f.root] = bar.close
            self._step(f, bar)
            snap = bar.ts_ns // _FIFTEEN_MIN
            if snap != last_snap:
                eq = self.equity()
                self.peak_equity = max(self.peak_equity, eq)
                equity_points.append((bar.ts, eq))
                last_snap = snap

        end_ts = max(f.ts[-1] for f in feeds)
        if self.close_at_end:
            for t in self.open_trades:  # close anything still open at the last price
                self.realised += self.execution.mark_to_market_close(t, self.last_close[t.plan.symbol], end_ts, "end of backtest")
                self._flush(t)
        equity_points.append((end_ts, self.equity()))
        eq = pd.Series(dict(equity_points)).sort_index()
        records = [t.record for t in self.trades if t.record.fills and not t.is_open]
        return BacktestResult(self.s, records, eq, self.bus.of_type(Decision), list(self.data), [s.name for s in self.strategies],
                              min(f.ts[0] for f in feeds), end_ts)

    def _step(self, f: _Feed, bar: Bar) -> None:
        trades = [t for t in self.open[f.root] if t.is_open]

        # 1. Rolling an open position costs a round trip of commission and slippage.
        if f.roll[bar.seq] and trades:
            self.realised -= self.execution.charge_roll(trades, bar, f.broker)

        # 2. Fills.
        for t in trades:
            self.realised += self.execution.work_orders(t, bar)

        self._watch_shock(f.root, bar)
        self._timed_exits(f, bar)

        # 3. Bar close: confirmations, timeouts, breakeven.
        for t in trades:
            if t.is_open:
                t.on_bar_close(bar, t.budget)

        # 4. Bookkeeping for trades that just closed, and the watches.
        for t in trades:
            self._flush(t)
            if not t.is_open:
                self._closed(t, f, bar)
        self.open[f.root] = [t for t in self.open[f.root] if t.is_open]
        self._update_watches(f, bar)

        # 5. Setup-timeframe bars completing on this minute.
        for si, k in f.setups.get(bar.seq, ()):
            strat = self.strategies[si]
            htf = self.setup_bars[(f.root, si)]
            atr_now = float(htf["trail_atr"].iat[k])
            self.last_atr[(f.root, strat.timeframe)] = atr_now
            for t in self.open[f.root]:
                if t.plan.timeframe == strat.timeframe:
                    t.on_setup_bar(float(htf["close"].iat[k]), atr_now, bar)
                    self._flush(t)
            for plan in strat.on_bar(f.root, htf, k, self):
                self._consider(plan, f, bar)

    def _before_weekend(self, bar: Bar) -> bool:
        w = self.s.execution.weekend
        if not w.flatten:
            return False
        et = bar.ts.tz_convert(ET)
        return et.weekday() == 4 and et.hour * 60 + et.minute >= 17 * 60 - w.minutes_before_close and et.hour < 17

    def _timed_exits(self, f: _Feed, bar: Bar) -> None:
        """Weekend close and stale-trade exits."""
        trades = [t for t in self.open[f.root] if t.is_open]
        if not trades:
            return
        if self._before_weekend(bar):
            for t in trades:
                t.force_exit(bar, "closed before the weekend, when gaps can jump past a stop")
                self._flush(t)
            return
        se = self.s.execution.stale_exit
        if se.hours <= 0:
            return
        for t in trades:
            opened = t.record.opened_at
            if not t.position or opened is None or t.breakeven_done or t.working("exit"):
                continue
            if bar.ts_ns - pd.Timestamp(opened).value < se.hours * 3600 * 10**9:
                continue
            progress = (bar.close - t.avg_entry) * t.side / t.r_points() if t.r_points() else 0.0
            if abs(progress) <= se.max_progress_r:
                t.force_exit(bar, f"went nowhere for {se.hours:g} hours ({progress:+.1f}R)")
                self._flush(t)

    def _watch_shock(self, root: str, bar: Bar) -> None:
        sp = self.s.risk.shock_pause
        if sp.range_multiple <= 0:
            return
        rng = bar.high - bar.low
        avg = self._range_avg.get(root)
        if avg is not None and self._range_n[root] >= 60 and avg > 0 and rng > sp.range_multiple * avg:
            self.shock_until_ns[root] = bar.ts_ns + sp.minutes * 60 * 10**9
            self.decide("risk", "shock", f"{root} moved {rng:.2f} in one minute, {rng / avg:.0f}x its normal range; "
                        f"new entries paused for {sp.minutes} minutes.", root, bar.ts)
        self._range_avg[root] = rng if avg is None else avg + (rng - avg) / 120
        self._range_n[root] += 1

    def _flush(self, t: Trade) -> None:
        self.execution.report(t)

    def _closed(self, t: Trade, f: _Feed, bar: Bar) -> None:
        self.risk_gate.on_trade_closed(t.record, self)
        reason = t.record.exit_reasons[-1] if t.record.exit_reasons else ""
        if not t.record.fills:
            return
        if reason == "stopped out":
            self._watch_target.append((t, bar.seq + WATCH_BARS_AFTER_STOP, f.root))
            if not t.record.is_reentry and self.s.execution.max_reentries > 0:
                self._watch_reentry.append({"plan": t.plan, "until": bar.seq + REENTRY_WINDOW_BARS, "extreme": bar.low if t.side == 1 else bar.high, "root": f.root})

    def _update_watches(self, f: _Feed, bar: Bar) -> None:
        keep = []
        for t, until, root in self._watch_target:
            if root != f.root:
                keep.append((t, until, root))
                continue
            hit = bar.high >= t.plan.target if t.side == 1 else bar.low <= t.plan.target
            if hit:
                t.record.stopped_then_target = True
            elif bar.seq >= until:
                t.record.stopped_then_target = False
            else:
                keep.append((t, until, root))
        self._watch_target = keep

        keep_r = []
        for w in self._watch_reentry:
            if w["root"] != f.root:
                keep_r.append(w)
                continue
            p: TradePlan = w["plan"]
            w["extreme"] = min(w["extreme"], bar.low) if p.direction == 1 else max(w["extreme"], bar.high)
            if bar.seq > w["until"]:
                continue
            lvl = p.confirm_level if p.confirm_level is not None else p.entry
            reclaimed = bar.close > lvl if p.direction == 1 else bar.close < lvl
            if not reclaimed:
                keep_r.append(w)
                continue
            a = self.last_atr.get((f.root, p.timeframe), 0.0) or 0.0
            stop = w["extreme"] - p.direction * self.s.execution.stops.atr_buffer * a
            new = TradePlan(p.symbol, p.direction, bar.close, stop, p.target, p.setup,
                            f"Re-entry: stopped out, then price closed back {'above' if p.direction == 1 else 'below'} {lvl:.2f}; the setup is still valid.",
                            p.timeframe, confirm_level=None, context={**p.context, "reentry": True})
            self._consider(new, f, bar, is_reentry=True)
        self._watch_reentry = keep_r

    # ----- accepting a trade idea ------------------------------------------------------------------------
    def _consider(self, plan: TradePlan, f: _Feed, bar: Bar, is_reentry: bool = False) -> None:
        sym = plan.symbol
        if self.trade_from_ns is not None and bar.ts_ns < self.trade_from_ns:
            return

        def reject(reason: str) -> None:
            self.decide("risk", "rejected", reason, sym, bar.ts, setup=plan.setup)

        rr_min = self.s.risk.min_reward_risk
        if plan.risk_points <= 0 or plan.reward_risk < rr_min - 1e-9:
            return reject(f"{plan.setup}: reward-to-risk {plan.reward_risk:.1f} is below the {rr_min:.0f}:1 minimum.")
        if bar.session == "break":
            return reject("No new entries during the daily maintenance break.")
        if self._before_weekend(bar):
            return reject("No new entries this close to the weekend: positions are closed before Friday's close.")
        windows = self.s.sessions.entry_windows
        if windows != "all" and not any(in_window(bar.ts, w.start, w.end) for w in windows):
            return reject("Outside the configured entry windows.")
        if bar.volume < self.s.execution.min_liquidity.min_volume_1m:
            return reject(f"Market too thin right now ({int(bar.volume)} contracts traded in the last minute).")
        if len(self.open_trades) >= self.s.risk.max_open_positions:
            return reject(f"Already at the maximum of {self.s.risk.max_open_positions} open positions.")
        verdict = self.risk_gate.check(plan, self, bar)
        if not verdict.allowed:
            return reject(verdict.reason)

        eq = self.equity()
        self.peak_equity = max(self.peak_equity, eq)
        pct = self.s.execution.score_sizing.pct_for(plan.context.get("score"), self.s.risk.risk_per_trade_pct)
        if pct < self.s.risk.risk_per_trade_pct:
            self.decide("risk", "sized", f"Scanner score {plan.context.get('score'):.0f}: risking {pct:g}% "
                        f"instead of {self.s.risk.risk_per_trade_pct:g}% (score-based sizing).", sym, bar.ts, setup=plan.setup)
        brake = self.s.risk.drawdown_brake
        dd = (self.peak_equity - eq) / self.peak_equity * 100 if self.peak_equity > 0 else 0.0
        if brake.start_pct > 0 and dd >= brake.start_pct:
            pct *= brake.risk_multiplier
            self.decide("risk", "brake", f"Drawdown brake: the account is {dd:.1f}% below its high of ${self.peak_equity:,.0f}, "
                        f"so this trade risks {pct:.2f}% instead of the usual amount.", sym, bar.ts, setup=plan.setup)
        budget = eq * pct / 100
        if verdict.max_budget is not None and verdict.max_budget < budget:
            self.decide("risk", "reduced", verdict.reason, sym, bar.ts, budget=budget, reduced_to=verdict.max_budget)
            budget = verdict.max_budget
        if self.reviewer is not None:
            rv = self.reviewer.review(plan, self, bar, budget)
            if rv.action == "reject":
                return self.decide("overseer", "rejected", rv.reason, sym, bar.ts, setup=plan.setup, source=rv.source)
            if rv.action == "reduce":
                self.decide("overseer", "reduced", rv.reason, sym, bar.ts, setup=plan.setup, factor=rv.size_factor, source=rv.source)
                budget *= rv.size_factor
            else:
                self.decide("overseer", "approved", rv.reason, sym, bar.ts, setup=plan.setup, source=rv.source)
        record = TradeRecord(id=len(self.trades) + 1, symbol=sym, setup=plan.setup, direction=plan.direction, reason=plan.reason,
                             planned_at=bar.ts, session=bar.session, initial_stop=plan.stop, target=plan.target,
                             risk_points=plan.risk_points, is_reentry=is_reentry, context=dict(plan.context))
        probe = Trade(record, plan, f.broker, self.s.execution, budget, "single")
        per = probe.per_contract_risk(plan.entry, bar.session)
        qty = math.floor(budget / per) if per > 0 else 0
        if qty < 1:
            return reject(f"{plan.setup}: even 1 contract would risk ${per:,.0f}, more than the ${budget:,.0f} allowed per trade.")
        mode = "thirds" if qty >= self.s.execution.scale_in.min_contracts_for_thirds else "single"
        trade = Trade(record, plan, f.broker, self.s.execution, budget, mode)
        record.mode = mode
        record.risk_dollars = budget
        self.trades.append(trade)
        self.open[sym].append(trade)
        side = "long" if plan.direction == 1 else "short"
        self.decide("scanner", "trade_idea", plan.reason, sym, bar.ts, setup=plan.setup, direction=side)
        self.decide("risk", "approved",
                    f"{side.title()} {sym}: risk ${budget:,.0f} ({budget / self.equity() * 100:.2f}% of equity), stop {plan.stop:.2f}, "
                    f"{plan.reward_risk:.1f}:1 to the first objective, up to {qty} contracts ({mode}).",
                    sym, bar.ts, trade_id=record.id)
        trade.start(bar, qty)
        self._flush(trade)
