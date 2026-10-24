"""The interface a strategy implements. In Phase 2 the Scanner Agent produces plans through the same interface."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from tradingdesk.backtest.models import TradePlan


class Context(Protocol):
    def has_trade(self, symbol: str) -> bool: ...


class Strategy:
    name = "base"
    timeframe = "15m"

    def prepare(self, symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to the setup-timeframe bars. Must only use past data (no look-ahead)."""
        return bars

    def on_bar(self, symbol: str, bars: pd.DataFrame, i: int, ctx: Context) -> list[TradePlan]:
        """Called when bar `i` of `bars` has just closed. Return zero or more trade ideas."""
        return []
