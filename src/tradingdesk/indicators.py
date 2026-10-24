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


def adx(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index: trend strength from 0 to 100 (above ~25 is a clear trend, below ~20 is choppy)."""
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = atr(bars, 1)  # true range
    a = 1 / period
    atr_s = tr.ewm(alpha=a, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=a, adjust=False, min_periods=period).mean()


def bollinger(series: pd.Series, period: int = 20, width: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(period, min_periods=period).mean()
    sd = series.rolling(period, min_periods=period).std(ddof=0)
    return mid - width * sd, mid, mid + width * sd


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Where the current value sits among the last `window` values, from 0 (lowest) to 1 (highest)."""
    return series.rolling(window, min_periods=max(10, window // 4)).rank(pct=True)
