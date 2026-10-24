"""Indicators used by strategies and the trade manager. All of them only look at the current and past bars."""

from __future__ import annotations

import pandas as pd


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    prev_close = bars["close"].shift()
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def prior_high(bars: pd.DataFrame, lookback: int) -> pd.Series:
    """Highest high of the `lookback` bars BEFORE the current one."""
    return bars["high"].shift(1).rolling(lookback, min_periods=lookback).max()


def prior_low(bars: pd.DataFrame, lookback: int) -> pd.Series:
    return bars["low"].shift(1).rolling(lookback, min_periods=lookback).min()
