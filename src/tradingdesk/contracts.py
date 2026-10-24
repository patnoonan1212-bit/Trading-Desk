"""Contract specifications for the micro futures we trade.

Values are CME's published specs. `point_value` is dollars per 1.0 move in price; `tick_value` is dollars per tick.
`months` are the listed contract months that carry liquidity (CME month codes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    name: str
    tick_size: float
    point_value: float
    months: str
    calendar: str  # pandas_market_calendars name, used to know holidays and early closes

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value

    def round_to_tick(self, price: float, direction: str = "nearest") -> float:
        """Snap a price onto the tick grid. direction: nearest | up | down."""
        n = price / self.tick_size
        if direction == "up":
            n = math.ceil(n - 1e-9)
        elif direction == "down":
            n = math.floor(n + 1e-9)
        else:
            n = round(n)
        return round(n * self.tick_size, 10)

    def dollars(self, points: float, contracts: int = 1) -> float:
        return points * self.point_value * contracts


CONTRACTS: dict[str, ContractSpec] = {
    "MES": ContractSpec("MES", "Micro E-mini S&P 500", tick_size=0.25, point_value=5.0, months="HMUZ", calendar="CME_Equity"),
    "MNQ": ContractSpec("MNQ", "Micro E-mini Nasdaq-100", tick_size=0.25, point_value=2.0, months="HMUZ", calendar="CME_Equity"),
    "MCL": ContractSpec("MCL", "Micro WTI Crude Oil", tick_size=0.01, point_value=100.0, months=MONTH_CODES, calendar="CMEGlobex_MicroCrude"),
    "MGC": ContractSpec("MGC", "Micro Gold", tick_size=0.10, point_value=10.0, months="GJMQVZ", calendar="CMEGlobex_Gold"),
}


def spec(symbol: str) -> ContractSpec:
    """Spec for a root ('MES') or an outright contract ('MESZ5', 'MESZ25')."""
    root = root_of(symbol)
    if root not in CONTRACTS:
        raise KeyError(f"Unknown contract {symbol!r}")
    return CONTRACTS[root]


def root_of(symbol: str) -> str:
    for root in sorted(CONTRACTS, key=len, reverse=True):
        if symbol.startswith(root):
            return root
    return symbol
