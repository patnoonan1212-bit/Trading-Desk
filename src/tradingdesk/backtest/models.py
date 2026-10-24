"""Data structures shared by strategies, the simulated broker and the trade manager.

Prices inside the engine are BACK-ADJUSTED continuous prices (see data/rollover.py). Within one contract they
move exactly like the real contract, so stops, targets and P&L are correct; every fill also records the real
contract price (`raw_price`) for the logs.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Side = Literal[1, -1]  # 1 = buy / long, -1 = sell / short
OrderType = Literal["market", "limit", "stop"]

_ids = itertools.count(1)


@dataclass
class TradePlan:
    """A trade idea from a strategy (Phase 2: from the Scanner), before sizing and risk checks."""

    symbol: str            # product root, e.g. "MES"
    direction: Side
    entry: float           # expected entry price (used for sizing and reward-to-risk)
    stop: float            # protective stop, beyond real structure
    target: float          # first objective; must be at least min_reward_risk away
    setup: str             # strategy / setup name
    reason: str            # plain-English explanation
    timeframe: str = "15m"
    confirm_level: float | None = None   # a confirm-timeframe close beyond this confirms the trade
    valid_for_bars: int = 3              # how many setup bars the idea stays valid if not triggered

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        return abs(self.target - self.entry) / self.risk_points if self.risk_points else 0.0


@dataclass
class Order:
    symbol: str
    side: Side
    qty: int
    type: OrderType
    price: float | None = None     # limit or stop price; None for market
    purpose: str = "entry"          # entry | add | stop | partial | exit | roll
    trade_id: int | None = None
    placed_at: pd.Timestamp | None = None
    id: int = field(default_factory=lambda: next(_ids))
    filled: int = 0
    active_from: int = 0            # bar sequence number from which the order may fill
    cancelled: bool = False

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    @property
    def done(self) -> bool:
        return self.cancelled or self.remaining <= 0


@dataclass
class Fill:
    order_id: int
    trade_id: int | None
    symbol: str
    side: Side
    qty: int
    price: float           # back-adjusted price
    raw_price: float       # actual contract price
    intended: float        # the price we were aiming for (order price, or bar open for market orders)
    ts: pd.Timestamp
    purpose: str
    commission: float
    contract: str = ""

    @property
    def slippage_points(self) -> float:
        """Positive = worse than intended."""
        return (self.price - self.intended) * self.side


@dataclass
class TradeRecord:
    """Everything about one trade, for reports and (Phase 5) the Learning Agent."""

    id: int
    symbol: str
    setup: str
    direction: Side
    reason: str
    planned_at: pd.Timestamp
    session: str
    initial_stop: float
    target: float
    risk_points: float = 0.0
    planned_contracts: int = 0
    mode: str = "single"              # thirds | single
    fills: list[Fill] = field(default_factory=list)
    exit_reasons: list[str] = field(default_factory=list)
    opened_at: pd.Timestamp | None = None
    closed_at: pd.Timestamp | None = None
    gross_pnl: float = 0.0
    commissions: float = 0.0
    roll_costs: float = 0.0
    max_favorable: float = 0.0         # best price move in our favour, in points
    max_adverse: float = 0.0           # worst move against us, in points
    stopped_then_target: bool | None = None  # stopped out, then price reached the original target anyway
    is_reentry: bool = False

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.commissions - self.roll_costs

    @property
    def max_contracts(self) -> int:
        pos = peak = 0
        for f in self.fills:
            pos += f.qty if f.purpose in ("entry", "add") else -f.qty
            peak = max(peak, pos)
        return peak

    def r_multiple(self, point_value: float) -> float:
        risk = self.risk_points * point_value * max(self.planned_contracts, 1)
        return self.net_pnl / risk if risk else 0.0
