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

Phase 1 applies the per-trade checks (reward-to-risk, position size, open-position count, liquidity and the
daily break). The full Risk Manager (daily and weekly loss limits, losing streaks, correlated exposure, news
blackouts) plugs in at the `RiskGate` step in Phase 2.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingdesk.backtest.broker_sim import Bar, SimBroker
from tradingdesk.backtest.models import TradePlan, TradeRecord
from tradingdesk.backtest.strategy import Strategy
from tradingdesk.backtest.trade import Trade
from tradingdesk.config import Settings
from tradingdesk.contracts import spec
from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import Decision
from tradingdesk.data.resample import resample
from tradingdesk.indicators import atr
from tradingdesk.timeutil import in_window, sessions_of

log = logging.getLogger(__name__)

WATCH_BARS_AFTER_STOP = 1380  # about one trading day of 1-minute bars
REENTRY_WINDOW_BARS = 60


def _ns(index: pd.DatetimeIndex) -> np.ndarray:
    """Timestamps as int64 nanoseconds, whatever resolution pandas chose to store them in."""
    return index.as_unit("ns").asi8


class RiskGate:
    """Hook for portfolio-level risk rules. Phase 1: allow everything. Phase 2: the Risk Manager Agent."""

    def check(self, plan: TradePlan, engine: Backtester, bar: Bar) -> str | None:
        """Return None to allow, or a plain-English reason to reject."""
        return None

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
    broker: SimBroker
    setups: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # 1m index -> [(strategy, setup bar)]

    def bar(self, i: int) -> Bar:
        return Bar(i, self.ts[i], self.o[i], self.h[i], self.lo[i], self.c[i], self.v[i], self.adj[i], self.session[i], self.contract[i])


class Backtester:
    def __init__(self, settings: Settings, strategies: list[Strategy], data: dict[str, pd.DataFrame], bus: EventBus | None = None, risk_gate: RiskGate | None = None) -> None:
        self.s = settings
        self.strategies = strategies
        self.data = data
        self.bus = bus or EventBus()
        self.risk_gate = risk_gate or RiskGate()
        self.realised = 0.0
        self.trades: list[Trade] = []
        self.open: dict[str, list[Trade]] = defaultdict(list)
        self.last_close: dict[str, float] = {}
        self.setup_bars: dict[tuple[str, int], pd.DataFrame] = {}
        self.last_atr: dict[tuple[str, str], float] = {}
        self._watch_target: list[tuple[Trade, int, str]] = []
        self._watch_reentry: list[dict] = []
        self._logged: dict[int, int] = defaultdict(int)

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
        c = self.s.costs
        for root, cont in self.data.items():
            if cont.empty:
                continue
            sp = spec(root)
            f = _Feed(
                root=root, ts=cont.index, o=cont["open"].to_numpy(float), h=cont["high"].to_numpy(float),
                lo=cont["low"].to_numpy(float), c=cont["close"].to_numpy(float), v=cont["volume"].to_numpy(float),
                adj=cont["adjustment"].to_numpy(float), roll=cont["roll"].to_numpy(bool), contract=cont["contract"].to_numpy(str),
                session=sessions_of(cont.index).to_numpy(str), broker=SimBroker(sp, c.commission_per_side, c.slippage_ticks),
            )
            for si, strat in enumerate(self.strategies):
                htf = strat.prepare(root, resample(cont, strat.timeframe))
                htf["trail_atr"] = atr(htf, self.s.execution.exits.trail.atr_period)
                self.setup_bars[(root, si)] = htf
                # The 1-minute bar on which each setup bar completes (its last minute).
                # Compare both in nanoseconds: pandas may store times at different resolutions.
                done_at = np.searchsorted(_ns(cont.index), _ns(pd.DatetimeIndex(htf["end"])), side="left") - 1
                for k, i in enumerate(done_at):
                    if i >= 0:
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
            snap = bar.ts.floor("15min")
            if snap != last_snap:
                equity_points.append((bar.ts, self.equity()))
                last_snap = snap

        end_ts = max(f.ts[-1] for f in feeds)
        for t in self.open_trades:  # close anything still open at the last price
            if t.position:
                price = self.last_close[t.plan.symbol]
                pnl = (price - t.avg_entry) * t.side * t.position * t.pv
                t.record.gross_pnl += pnl
                t.record.commissions += t.position * t.broker.commission
                self.realised += pnl - t.position * t.broker.commission
                t.position = 0
            t.close(end_ts, "end of backtest")
            self._flush(t)
        equity_points.append((end_ts, self.equity()))
        eq = pd.Series(dict(equity_points)).sort_index()
        records = [t.record for t in self.trades if t.record.fills]
        return BacktestResult(self.s, records, eq, self.bus.of_type(Decision), list(self.data), [s.name for s in self.strategies],
                              min(f.ts[0] for f in feeds), end_ts)

    def _step(self, f: _Feed, bar: Bar) -> None:
        trades = [t for t in self.open[f.root] if t.is_open]

        # 1. Rolling an open position costs a round trip of commission and slippage.
        if f.roll[bar.seq] and trades:
            for t in trades:
                if t.position:
                    cost = t.position * (2 * f.broker.commission + 2 * f.broker.slippage(bar.session) * t.pv)
                    t.record.roll_costs += cost
                    self.realised -= cost
                    t.note(bar.ts, "roll", f"Contract rolled to {bar.contract}; position carried over (cost ${cost:.2f}).")

        # 2. Fills.
        for t in trades:
            self._fill(t, bar)

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

    def _fill(self, t: Trade, bar: Bar) -> None:
        b = t.broker
        # Market orders (entries, adds, exits) fill first at the open.
        for o in [o for o in t.orders if o.type == "market" and not o.done]:
            fill = b.try_fill(o, bar)
            if fill:
                self.realised += t.on_fill(fill, bar) - fill.commission
        if not t.is_open:
            return
        stop = t.working("stop")
        partial = t.working("partial")
        # If the stop and the profit target are both reached in the same minute, assume the stop came first.
        if stop and b.touches(stop, bar) and bar.seq >= stop.active_from:
            stop_qty = stop.remaining
            fill = b.try_fill(stop, bar)
            if fill and fill.qty < stop_qty:  # exits are never limited by volume
                stop.filled = stop.qty
                fill.qty = stop_qty
                fill.commission = stop_qty * b.commission
            if fill:
                self.realised += t.on_fill(fill, bar) - fill.commission
            return
        if partial:
            fill = b.try_fill(partial, bar)
            if fill:
                self.realised += t.on_fill(fill, bar) - fill.commission

    def _flush(self, t: Trade) -> None:
        """Send any new notes from a trade to the decision log."""
        n = self._logged[t.record.id]
        for ts, action, reason in t.log[n:]:
            self.decide("execution", action, reason, t.plan.symbol, ts, trade_id=t.record.id)
        self._logged[t.record.id] = len(t.log)

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
                            p.timeframe, confirm_level=None)
            self._consider(new, f, bar, is_reentry=True)
        self._watch_reentry = keep_r

    # ----- accepting a trade idea ------------------------------------------------------------------------
    def _consider(self, plan: TradePlan, f: _Feed, bar: Bar, is_reentry: bool = False) -> None:
        sym = plan.symbol

        def reject(reason: str) -> None:
            self.decide("risk", "rejected", reason, sym, bar.ts, setup=plan.setup)

        rr_min = self.s.risk.min_reward_risk
        if plan.risk_points <= 0 or plan.reward_risk < rr_min - 1e-9:
            return reject(f"{plan.setup}: reward-to-risk {plan.reward_risk:.1f} is below the {rr_min:.0f}:1 minimum.")
        if bar.session == "break":
            return reject("No new entries during the daily maintenance break.")
        windows = self.s.sessions.entry_windows
        if windows != "all" and not any(in_window(bar.ts, w.start, w.end) for w in windows):
            return reject("Outside the configured entry windows.")
        if bar.volume < self.s.execution.min_liquidity.min_volume_1m:
            return reject(f"Market too thin right now ({int(bar.volume)} contracts traded in the last minute).")
        if len(self.open_trades) >= self.s.risk.max_open_positions:
            return reject(f"Already at the maximum of {self.s.risk.max_open_positions} open positions.")
        gate = self.risk_gate.check(plan, self, bar)
        if gate:
            return reject(gate)

        budget = self.equity() * self.s.risk.risk_per_trade_pct / 100
        record = TradeRecord(id=len(self.trades) + 1, symbol=sym, setup=plan.setup, direction=plan.direction, reason=plan.reason,
                             planned_at=bar.ts, session=bar.session, initial_stop=plan.stop, target=plan.target,
                             risk_points=plan.risk_points, is_reentry=is_reentry)
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
                    f"{side.title()} {sym}: risk ${budget:,.0f} ({self.s.risk.risk_per_trade_pct}% of equity), stop {plan.stop:.2f}, "
                    f"{plan.reward_risk:.1f}:1 to the first objective, up to {qty} contracts ({mode}).",
                    sym, bar.ts, trade_id=record.id)
        trade.start(bar, qty)
        self._flush(trade)
