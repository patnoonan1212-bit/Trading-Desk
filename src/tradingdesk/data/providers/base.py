"""The interface every market data source implements.

The rest of the system only talks to this interface, so Databento, Tradovate or any other source can be
swapped without touching agents or the backtester.

Bars come back as a DataFrame with one row per contract per minute:
    ts (UTC, bar open time) | symbol (outright, e.g. MESZ5) | open | high | low | close | volume
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

BAR_COLUMNS = ["ts", "symbol", "open", "high", "low", "close", "volume"]


class ProviderError(RuntimeError):
    pass


class HistoricalProvider(ABC):
    name: str = "base"

    def estimate_cost(self, root: str, start: pd.Timestamp, end: pd.Timestamp, schema: str) -> float:
        """Dollar cost of a download, if the source charges. 0 for free sources."""
        return 0.0

    @abstractmethod
    def fetch_bars(self, root: str, start: pd.Timestamp, end: pd.Timestamp, schema: str = "ohlcv-1m") -> pd.DataFrame:
        """All outright contracts of `root` between start and end, as 1-minute bars."""


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS})
    df["ts"] = pd.Series(dtype="datetime64[ns, UTC]")
    df["symbol"] = pd.Series(dtype="object")
    return df


def utc(ts) -> pd.Timestamp:
    """A timestamp in UTC; naive values are taken to already be UTC."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
