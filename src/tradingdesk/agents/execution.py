"""The Execution Agent: turns approved trades into orders at a broker and keeps the books from the fills.

Every trade's orders (entries, adds, the protective stop, the partial-profit limit, exits) go through this
agent to a broker. Which broker depends on the mode:

    backtest, paper   SimBroker: fills worked out from each 1-minute bar with cautious rules (see broker_sim.py)
    live              Tradovate (Phase 6). It can only be created when you set `mode: live` yourself AND the
                      broker login is in the environment; until Phase 6 it refuses to start at all.

So paper trading on live prices runs exactly the same code, with the same fill rules, as a backtest. That is
the point of paper trading here: to check that what happens on fresh prices matches what the backtest promised.

Before an order is sent, the agent checks it is sane (a positive size no bigger than
`execution.max_contracts_per_order`, a price where one is needed). An order that fails is refused, logged
and, if it was an entry, the trade is cancelled.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

from tradingdesk.backtest.broker_sim import Bar, SimBroker
from tradingdesk.backtest.models import Fill, Order
from tradingdesk.config import Settings
from tradingdesk.contracts import ContractSpec

if TYPE_CHECKING:
    from tradingdesk.backtest.trade import Trade


class Broker(Protocol):
    """What the Execution Agent needs from any broker, simulated or real."""

    spec: ContractSpec
    commission: float

    def slippage(self, session: str) -> float: ...
    def try_fill(self, order: Order, bar: Bar) -> Fill | None: ...
    def touches(self, order: Order, bar: Bar) -> bool: ...


class LiveTradingNotAvailable(RuntimeError):
    pass


def make_broker(settings: Settings, spec: ContractSpec) -> Broker:
    if settings.mode == "live":
        # Settings already refuse live mode without broker credentials; the real broker connection is Phase 6.
        raise LiveTradingNotAvailable("Live trading through Tradovate is not built yet (Phase 6). Set mode back to paper.")
    return SimBroker(spec, settings.costs.commission_per_side, settings.costs.slippage_ticks)


class ExecutionAgent:
    name = "execution"

    def __init__(self, settings: Settings, decide) -> None:
        self.s = settings
        self.decide = decide            # callback into the shared decision log
        self._logged: dict[int, int] = {}
        self._vetted: set[int] = set()

    def broker_for(self, spec: ContractSpec) -> Broker:
        return make_broker(self.s, spec)

    # ----- order checks --------------------------------------------------------------------------------
    def _vet(self, t: Trade, o: Order, bar: Bar) -> bool:
        if o.id in self._vetted:
            return True
        cap = self.s.execution.max_contracts_per_order
        problem = ""
        if o.qty < 1:
            problem = f"size {o.qty} is not a positive number of contracts"
        elif o.qty > cap:
            problem = f"size {o.qty} is above the {cap}-contract safety limit per order"
        elif o.type != "market" and (o.price is None or not math.isfinite(o.price) or o.price <= 0):
            problem = f"a {o.type} order needs a valid price"
        if problem:
            o.cancelled = True
            t.note(bar.ts, "order_refused", f"Refused {o.purpose} order for {t.plan.symbol}: {problem}.")
            if o.purpose in ("entry", "add") and not t.position:
                t.close(bar.ts, "order refused by the Execution Agent's safety check")
            return False
        self._vetted.add(o.id)
        return True

    # ----- each bar ------------------------------------------------------------------------------------
    def charge_roll(self, trades: list[Trade], bar: Bar, broker: Broker) -> float:
        """Rolling an open position to the next contract costs a round trip of commission and slippage."""
        total = 0.0
        for t in trades:
            if t.position:
                cost = t.position * (2 * broker.commission + 2 * broker.slippage(bar.session) * t.pv)
                t.record.roll_costs += cost
                total += cost
                t.note(bar.ts, "roll", f"Contract rolled to {bar.contract}; position carried over (cost ${cost:.2f}).")
        return total

    def work_orders(self, t: Trade, bar: Bar) -> float:
        """Send working orders to the broker for this bar and apply the fills. Returns realised P&L after commissions."""
        b = t.broker
        realised = 0.0
        # Market orders (entries, adds, exits) fill first at the open.
        for o in [o for o in t.orders if o.type == "market" and not o.done]:
            if not self._vet(t, o, bar):
                continue
            fill = b.try_fill(o, bar)
            if fill:
                realised += t.on_fill(fill, bar) - fill.commission
        if not t.is_open:
            return realised
        stop = t.working("stop")
        partial = t.working("partial")
        if stop and not self._vet(t, stop, bar):
            stop = None
        if partial and not self._vet(t, partial, bar):
            partial = None
        # If the stop and the profit target are both reached in the same minute, assume the stop came first.
        if stop and b.touches(stop, bar) and bar.seq >= stop.active_from:
            stop_qty = stop.remaining
            fill = b.try_fill(stop, bar)
            if fill and fill.qty < stop_qty:  # exits are never limited by volume
                stop.filled = stop.qty
                fill.qty = stop_qty
                fill.commission = stop_qty * b.commission
            if fill:
                realised += t.on_fill(fill, bar) - fill.commission
            return realised
        if partial:
            fill = b.try_fill(partial, bar)
            if fill:
                realised += t.on_fill(fill, bar) - fill.commission
        return realised

    def mark_to_market_close(self, t: Trade, price: float, ts, reason: str) -> float:
        """Close a position at `price` without a broker fill (end of a backtest). Returns realised P&L after commissions."""
        realised = 0.0
        if t.position:
            pnl = (price - t.avg_entry) * t.side * t.position * t.pv
            t.record.gross_pnl += pnl
            t.record.commissions += t.position * t.broker.commission
            realised = pnl - t.position * t.broker.commission
            t.position = 0
        t.close(ts, reason)
        return realised

    def report(self, t: Trade) -> None:
        """Send any new notes from a trade to the decision log."""
        n = self._logged.get(t.record.id, 0)
        for ts, action, reason in t.log[n:]:
            self.decide("execution", action, reason, t.plan.symbol, ts, trade_id=t.record.id)
        self._logged[t.record.id] = len(t.log)
