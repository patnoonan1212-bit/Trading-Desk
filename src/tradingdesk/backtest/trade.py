"""The life of one trade: entering (with or without scaling in), protecting, taking partial profit, trailing.

Entry rules (from the config):
  thirds mode (the trade can be at least `min_contracts_for_thirds` contracts):
      1. buy/sell ~1/3 right away and put the stop in at the same time
      2. add the rest only after a confirm-timeframe close beyond the confirmation level, sized from the risk
         budget that's left, so total risk across all entries never exceeds the per-trade limit
      3. if confirmation doesn't come within `confirm_timeout_bars`, close the starter
  single mode (smaller trades): wait for the same confirmation, then enter once with the full size.

Exit rules:
  - the stop is always working from the moment any contracts are held
  - at 2R: take partial profit (thirds mode) and move the stop to breakeven
  - after that, trail the stop by ATR on the setup timeframe; it only ever moves in our favour
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tradingdesk.backtest.broker_sim import Bar, SimBroker
from tradingdesk.backtest.models import Fill, Order, TradePlan, TradeRecord
from tradingdesk.config import Execution


@dataclass
class Trade:
    record: TradeRecord
    plan: TradePlan
    broker: SimBroker
    rules: Execution
    budget: float                     # dollars this trade may lose in total
    mode: str                         # thirds | single
    state: str = "waiting"            # waiting -> starter -> full -> closed
    position: int = 0
    avg_entry: float = 0.0
    stop: float = 0.0
    orders: list[Order] = field(default_factory=list)
    waited: int = 0
    breakeven_done: bool = False
    partial_done: bool = False
    exit_reason: str = ""
    log: list[tuple] = field(default_factory=list)  # (ts, action, reason) for the decision log

    def __post_init__(self) -> None:
        self.stop = self.plan.stop

    # ----- helpers -------------------------------------------------------------------------------------
    @property
    def side(self) -> int:
        return self.plan.direction

    @property
    def pv(self) -> float:
        return self.broker.spec.point_value

    @property
    def is_open(self) -> bool:
        return self.state != "closed"

    def note(self, ts, action: str, reason: str) -> None:
        self.log.append((ts, action, reason))

    def per_contract_risk(self, entry: float, session: str) -> float:
        """Dollars lost per contract if stopped: distance to stop plus round-trip costs."""
        slip = self.broker.slippage(session)
        return abs(entry - self.stop) * self.pv + 2 * self.broker.commission + 2 * slip * self.pv

    def risk_used(self, session: str) -> float:
        if not self.position:
            return 0.0
        return self.position * (abs(self.avg_entry - self.stop) * self.pv + 2 * self.broker.commission + 2 * self.broker.slippage(session) * self.pv)

    def working(self, purpose: str) -> Order | None:
        return next((o for o in self.orders if o.purpose == purpose and not o.done), None)

    def place(self, purpose: str, side: int, qty: int, type_: str, price: float | None, bar: Bar) -> Order:
        o = Order(self.plan.symbol, side, qty, type_, price, purpose, self.record.id, bar.ts, active_from=bar.seq + 1)
        self.orders.append(o)
        return o

    def cancel(self, purpose: str) -> None:
        for o in self.orders:
            if o.purpose == purpose and not o.done:
                o.cancelled = True

    def confirmed(self, close: float) -> bool:
        lvl = self.plan.confirm_level
        return True if lvl is None else (close > lvl if self.side == 1 else close < lvl)

    def r_points(self) -> float:
        return abs(self.avg_entry - self.plan.stop)

    def two_r_level(self) -> float:
        return self.avg_entry + self.side * self.rules.exits.partial_at_r * self.r_points()

    # ----- order placement -----------------------------------------------------------------------------
    def start(self, bar: Bar, planned_qty: int) -> None:
        """Called on the bar where the plan was accepted (its close). Orders work from the next bar."""
        self.record.planned_contracts = planned_qty
        if self.mode == "thirds":
            starter = max(1, round(planned_qty * self.rules.scale_in.starter_fraction))
            self.place("entry", self.side, starter, "market", None, bar)
            self.state = "starter"
            self.note(bar.ts, "starter_order", f"Buying {starter} of {planned_qty} planned contracts now; the rest waits for confirmation." if self.side == 1
                      else f"Selling {starter} of {planned_qty} planned contracts now; the rest waits for confirmation.")
        else:
            self.state = "waiting"
            self.note(bar.ts, "waiting", f"Trade is {planned_qty} contract(s), too small to split in thirds; entering once after confirmation.")

    def _sync_exit_orders(self, bar: Bar) -> None:
        """Keep one stop for the whole position, and a partial-profit limit at 2R in thirds mode."""
        self.cancel("stop")
        if self.position > 0:
            o = self.place("stop", -self.side, self.position, "stop", self.stop, bar)
            o.active_from = bar.seq  # protection is live immediately
        self.cancel("partial")
        if self.mode == "thirds" and not self.partial_done and self.position >= 2:
            qty = max(1, round(self.position * self.rules.exits.partial_fraction))
            self.place("partial", -self.side, qty, "limit", self.broker.spec.round_to_tick(self.two_r_level()), bar)

    # ----- events --------------------------------------------------------------------------------------
    def on_fill(self, f: Fill, bar: Bar) -> float:
        """Apply a fill. Returns realised gross P&L in dollars."""
        self.record.fills.append(f)
        self.record.commissions += f.commission
        realised = 0.0
        if f.purpose in ("entry", "add"):
            new_pos = self.position + f.qty
            self.avg_entry = (self.avg_entry * self.position + f.price * f.qty) / new_pos
            self.position = new_pos
            if self.record.opened_at is None:
                self.record.opened_at = f.ts
            self._sync_exit_orders(bar)
        else:
            qty = min(f.qty, self.position)
            realised = (f.price - self.avg_entry) * self.side * qty * self.pv
            self.position -= qty
            self.record.gross_pnl += realised
            if f.purpose == "partial":
                self.partial_done = True
                self.note(f.ts, "partial_profit", f"Took {qty} contract(s) off at {f.price:.2f} (2R).")
                self._sync_exit_orders(bar)
            if self.position == 0:
                reason = {"stop": "stopped out" if not self.breakeven_done else "trailing/breakeven stop hit",
                          "exit": self.exit_reason or "exit", "partial": "final partial"}.get(f.purpose, f.purpose)
                self.close(f.ts, reason)
            elif f.purpose == "stop":  # partially filled stop: keep protecting the rest
                self._sync_exit_orders(bar)
        return realised

    def close(self, ts, reason: str) -> None:
        for o in self.orders:
            if not o.done:
                o.cancelled = True
        self.state = "closed"
        self.record.closed_at = ts
        self.record.exit_reasons.append(reason)
        self.note(ts, "closed", reason)

    def force_exit(self, bar: Bar, reason: str) -> None:
        """Leave now at market (weekend, stale trade): stop adding, cancel targets, close what's held."""
        if self.state == "closed":
            return
        self.cancel("entry")
        self.cancel("add")
        self.cancel("partial")
        if not self.position:
            self.close(bar.ts, reason)
        elif not self.working("exit"):
            self.exit_reason = reason
            self.place("exit", -self.side, self.position, "market", None, bar)
            self.state = "full"
            self.note(bar.ts, "exit", f"Closing {self.position} contract(s): {reason}.")

    def on_bar_close(self, bar: Bar, budget_left: float) -> None:
        """Confirm-timeframe (1-minute) bar closed: confirmation, timeout, breakeven and excursion tracking."""
        if self.state == "closed":
            return
        # Track how far price went for and against us, in points.
        if self.position:
            fav = (bar.high - self.avg_entry) if self.side == 1 else (self.avg_entry - bar.low)
            adv = (self.avg_entry - bar.low) if self.side == 1 else (bar.high - self.avg_entry)
            self.record.max_favorable = max(self.record.max_favorable, fav)
            self.record.max_adverse = max(self.record.max_adverse, adv)

        if self.state in ("waiting", "starter") and not self.working("entry"):
            self.waited += 1
            if self.confirmed(bar.close):
                qty = self._size_at(bar.close, bar.session, budget_left)
                if self.state == "waiting":
                    if qty <= 0:
                        self.close(bar.ts, "confirmed, but the trade no longer fits the risk budget")
                        return
                    self.place("entry", self.side, qty, "market", None, bar)
                    self.record.planned_contracts = qty
                    self.note(bar.ts, "confirmed_entry", f"1m close {bar.close:.2f} confirmed the setup; entering {qty} contract(s).")
                else:
                    if qty > 0:
                        self.place("add", self.side, qty, "market", None, bar)
                        self.note(bar.ts, "confirmed_add", f"1m close {bar.close:.2f} confirmed; adding {qty} contract(s) within the remaining risk budget.")
                    else:
                        self.note(bar.ts, "confirmed_no_add", "Confirmed, but no risk budget left to add; keeping the starter only.")
                self.state = "full"
            elif self.waited >= self.rules.scale_in.confirm_timeout_bars:
                if self.state == "waiting":
                    self.close(bar.ts, f"no confirmation within {self.waited} bars, trade skipped")
                elif self.position:
                    self.exit_reason = f"no confirmation within {self.waited} bars, starter closed"
                    self.place("exit", -self.side, self.position, "market", None, bar)
                    self.cancel("partial")
                    self.state = "full"  # nothing more to add; the exit order finishes it

        # Breakeven once price has reached 2R.
        if self.position and not self.breakeven_done:
            reached = bar.high >= self.two_r_level() if self.side == 1 else bar.low <= self.two_r_level()
            if reached:
                self.breakeven_done = True
                be = self.broker.spec.round_to_tick(self.avg_entry, "up" if self.side == 1 else "down")
                if (be - self.stop) * self.side > 0:
                    self.stop = be
                    self.note(bar.ts, "breakeven", f"Price reached 2R; stop moved to breakeven {be:.2f}.")
                    self._sync_exit_orders(bar)

    def _size_at(self, entry: float, session: str, budget_left: float) -> int:
        remaining = min(budget_left, self.budget - self.risk_used(session))
        per = self.per_contract_risk(entry, session)
        if per <= 0:
            return 0
        return max(0, math.floor(remaining / per))

    def on_setup_bar(self, close: float, atr_value: float, bar: Bar) -> None:
        """A setup-timeframe bar closed: trail the stop once breakeven is in."""
        if not (self.position and self.breakeven_done) or not atr_value or math.isnan(atr_value):
            return
        cand = close - self.side * self.rules.exits.trail.atr_multiple * atr_value
        cand = self.broker.spec.round_to_tick(cand, "down" if self.side == 1 else "up")
        if (cand - self.stop) * self.side > 0:
            self.note(bar.ts, "trail", f"Trailing stop moved from {self.stop:.2f} to {cand:.2f}.")
            self.stop = cand
            self._sync_exit_orders(bar)
