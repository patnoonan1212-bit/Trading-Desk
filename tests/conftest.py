import numpy as np
import pandas as pd
import pytest

from tradingdesk.config import load_settings


@pytest.fixture
def settings():
    return load_settings(mode="backtest")


def minute_bars(rows, start="2024-03-05 14:30", contract="MESH4", adjustment=0.0):
    """Hand-made continuous 1-minute bars. rows: list of (open, high, low, close[, volume])."""
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz="UTC")
    o, h, lo, c = (np.array([r[k] for r in rows], dtype=float) for k in range(4))
    v = np.array([r[4] if len(r) > 4 else 500 for r in rows], dtype=float)
    df = pd.DataFrame(
        {"open": o, "high": h, "low": lo, "close": c, "raw_open": o - adjustment, "raw_high": h - adjustment,
         "raw_low": lo - adjustment, "raw_close": c - adjustment, "volume": v, "contract": contract,
         "trade_date": pd.Timestamp(start).normalize(), "roll": False, "adjustment": adjustment},
        index=idx,
    )
    return df


def raw_bars(rows):
    """Raw outright bars. rows: list of (ts, symbol, open, high, low, close, volume)."""
    df = pd.DataFrame(rows, columns=["ts", "symbol", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df
