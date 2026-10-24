"""Simulated broker: decides whether and at what price working orders fill on each 1-minute bar.

Rules (all deliberately on the cautious side):
  market  fills at the next bar's open, plus slippage against us
  stop    triggers when the bar trades at or through the stop; fills at the stop or at the open if the bar
          gapped past it, plus slippage
  limit   fills only if the bar trades THROUGH the limit (touching isn't enough, we may be at the back of the
          queue); fills at the limit, or at the open if it opened better; no slippage
  size    at most `participation` of the bar's volume fills per bar (at least 1 contract); the rest keeps
          working, which models partial fills
Orders become active only from the bar after they were placed, so nothing fills on information from the
bar that created it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingdesk.backtest.models import Fill, Order
from tradingdesk.contracts import ContractSpec


@dataclass
class Bar:
    seq: int
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment: float
    session: str
    contract: str


class SimBroker:
    def __init__(self, spec: ContractSpec, commission_per_side: float, slippage_ticks: dict[str, float], participation: float = 0.25) -> None:
        self.spec = spec
        self.commission = commission_per_side
        self.slippage_ticks = slippage_ticks
        self.participation = participation

    def slippage(self, session: str) -> float:
        ticks = self.slippage_ticks.get(session, max(self.slippage_ticks.values()))
        return ticks * self.spec.tick_size

    def max_fill(self, bar: Bar, remaining: int) -> int:
        return min(remaining, max(1, int(bar.volume * self.participation)))

    def try_fill(self, order: Order, bar: Bar) -> Fill | None:
        """Fill (part of) `order` on `bar`, or return None if it doesn't trade."""
        if order.done or bar.seq < order.active_from:
            return None
        side, slip = order.side, self.slippage(bar.session)
        if order.type == "market":
            intended = bar.open
            price = bar.open + side * slip
        elif order.type == "stop":
            p = order.price
            if side == 1 and bar.high >= p:
                intended, price = p, max(p, bar.open) + slip
            elif side == -1 and bar.low <= p:
                intended, price = p, min(p, bar.open) - slip
            else:
                return None
            # A stop can't fill outside the bar's range by more than the slippage we assume.
            price = min(price, bar.high + slip) if side == 1 else max(price, bar.low - slip)
        else:  # limit
            p = order.price
            if side == 1 and bar.low < p:
                intended, price = p, min(p, bar.open)
            elif side == -1 and bar.high > p:
                intended, price = p, max(p, bar.open)
            else:
                return None
        qty = self.max_fill(bar, order.remaining)
        price = self.spec.round_to_tick(price, "up" if side == 1 else "down")
        order.filled += qty
        return Fill(
            order_id=order.id, trade_id=order.trade_id, symbol=order.symbol, side=side, qty=qty,
            price=price, raw_price=round(price - bar.adjustment, 10), intended=intended, ts=bar.ts,
            purpose=order.purpose, commission=qty * self.commission, contract=bar.contract,
        )

    def touches(self, order: Order, bar: Bar) -> bool:
        """Would this stop/limit be reached within the bar? (Used to resolve stop-vs-target in one bar.)"""
        if order.type == "stop":
            return bar.high >= order.price if order.side == 1 else bar.low <= order.price
        if order.type == "limit":
            return bar.low < order.price if order.side == 1 else bar.high > order.price
        return True
