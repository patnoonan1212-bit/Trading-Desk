"""Time helpers. Everything is stored in UTC; sessions are defined in US Eastern time like the CME schedule."""

from __future__ import annotations

from datetime import time

import pandas as pd

ET = "America/New_York"

# Session names used for slippage, reporting and (optionally) entry windows. Times are Eastern.
#   overnight  18:00-03:00 and 16:00-17:00
#   london     03:00-09:30
#   us_regular 09:30-16:00
#   break      17:00-18:00 (CME daily maintenance, no trading)
_LONDON = time(3, 0)
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_BREAK = time(17, 0)
_REOPEN = time(18, 0)


def to_et(ts: pd.Timestamp | pd.DatetimeIndex):
    return ts.tz_convert(ET)


def session_of(ts_utc: pd.Timestamp) -> str:
    t = ts_utc.tz_convert(ET).time()
    if _BREAK <= t < _REOPEN:
        return "break"
    if _LONDON <= t < _RTH_OPEN:
        return "london"
    if _RTH_OPEN <= t < _RTH_CLOSE:
        return "us_regular"
    return "overnight"


def sessions_of(index: pd.DatetimeIndex) -> pd.Series:
    """Vectorised session labels for a UTC DatetimeIndex."""
    et = index.tz_convert(ET)
    minutes = et.hour * 60 + et.minute
    labels = pd.Series("overnight", index=index)
    labels[(minutes >= 3 * 60) & (minutes < 9 * 60 + 30)] = "london"
    labels[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)] = "us_regular"
    labels[(minutes >= 17 * 60) & (minutes < 18 * 60)] = "break"
    return labels


def trading_date(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """CME trade date: a session that opens at 18:00 ET belongs to the next calendar day."""
    return (index.tz_convert(ET) + pd.Timedelta(hours=6)).normalize().tz_localize(None)


def in_window(ts_utc: pd.Timestamp, start: time, end: time) -> bool:
    t = ts_utc.tz_convert(ET).time()
    return start <= t < end if start <= end else (t >= start or t < end)
