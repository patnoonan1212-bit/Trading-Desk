"""Turn the continuous 1-minute series into 5m, 15m, 1h and daily bars.

Each bar is labelled by its start time and carries `end`, the moment it is complete. The backtester only
hands a bar to a strategy after `end`, so a 5-minute bar is never seen before its last minute has closed.
Daily bars follow the CME trade date (18:00 ET to 17:00 ET), not the calendar day.
"""

from __future__ import annotations

import pandas as pd

TIMEFRAMES = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "1d": "1D"}

_AGG = {
    "open": "first", "high": "max", "low": "min", "close": "last",
    "raw_open": "first", "raw_high": "max", "raw_low": "min", "raw_close": "last",
    "volume": "sum", "contract": "last", "trade_date": "last",
}


def resample(cont: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe!r}. Use one of {list(TIMEFRAMES)}")
    cols = {k: v for k, v in _AGG.items() if k in cont.columns}
    if timeframe == "1m":
        out = cont[list(cols)].copy()
        out["end"] = out.index + pd.Timedelta(minutes=1)
        return out
    if timeframe == "1d":
        g = cont.groupby("trade_date")
        out = g.agg(cols | {"trade_date": "last"})
        out["start"] = g.apply(lambda x: x.index[0])
        out["end"] = g.apply(lambda x: x.index[-1]) + pd.Timedelta(minutes=1)
        return out.set_index("start").rename_axis("ts")
    rule = TIMEFRAMES[timeframe]
    out = cont.resample(rule, label="left", closed="left").agg(cols).dropna(subset=["open"])
    out["end"] = out.index + pd.Timedelta(rule)
    return out
