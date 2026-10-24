"""Made-up but realistic-looking futures data, for tests and demos without a data subscription.

It mimics the things that matter for the pipeline and backtester: the 23-hour Globex day with the 17:00 ET
break and weekend close, busier and more volatile US regular hours, quiet overnight minutes with no trades,
alternating trending and choppy stretches, and several overlapping contract months whose volume shifts to
the next contract before expiry (so rollovers can be tested). Results on this data mean nothing for real
trading; it only proves the machinery works.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingdesk.contracts import MONTH_CODES, spec
from tradingdesk.data.providers.base import BAR_COLUMNS, HistoricalProvider, empty_bars, utc
from tradingdesk.data.symbols import third_friday
from tradingdesk.timeutil import ET

# start price, annual volatility, annual carry (basis drift per year, fraction of price)
_PROFILE = {
    "MES": (5000.0, 0.16, 0.020),
    "MNQ": (18000.0, 0.22, 0.020),
    "MCL": (75.0, 0.35, -0.040),
    "MGC": (2000.0, 0.15, 0.040),
}
_SESSION_VOL = {"us_regular": 1.0, "london": 0.7, "overnight": 0.45}
_SESSION_VOLUME = {"us_regular": 900.0, "london": 220.0, "overnight": 60.0}


def _globex_minutes(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    idx = pd.date_range(start.floor("min"), end, freq="1min", inclusive="left", tz="UTC")
    et = idx.tz_convert(ET)
    dow, minute = et.dayofweek, et.hour * 60 + et.minute
    open_ = ~((minute >= 17 * 60) & (minute < 18 * 60))  # daily break
    open_ &= ~(dow == 5)  # Saturday
    open_ &= ~((dow == 4) & (minute >= 17 * 60))  # Friday after close
    open_ &= ~((dow == 6) & (minute < 18 * 60))  # Sunday before open
    return idx[open_]


def _expiry(root: str, year: int, month: int) -> pd.Timestamp:
    if root == "MCL":  # crude stops trading around the 20th of the month before delivery
        prev = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
        return pd.Timestamp(year=prev.year, month=prev.month, day=20)
    return third_friday(year, month)


def _contracts(root: str, start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[str, pd.Timestamp]]:
    out = []
    for year in range(start.year - 1, end.year + 2):
        for code in spec(root).months:
            month = MONTH_CODES.index(code) + 1
            exp = _expiry(root, year, month).tz_localize(ET).tz_convert("UTC") + pd.Timedelta(hours=13)
            if exp > start and exp - pd.Timedelta(days=200) < end:
                out.append((f"{root}{code}{year % 10}", exp))
    return sorted(out, key=lambda x: x[1])


class SyntheticProvider(HistoricalProvider):
    name = "synthetic"

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    def fetch_bars(self, root, start, end, schema="ohlcv-1m") -> pd.DataFrame:
        start, end = utc(start), utc(end)
        idx = _globex_minutes(start, end)
        if len(idx) == 0:
            return empty_bars()
        sp = spec(root)
        price0, vol, carry = _PROFILE[root]
        rng = np.random.default_rng(self.seed + sum(map(ord, root)) + int(start.timestamp() // 86400))

        et = idx.tz_convert(ET)
        minute = et.hour * 60 + et.minute
        session = np.where((minute >= 570) & (minute < 960), "us_regular", np.where((minute >= 180) & (minute < 570), "london", "overnight"))
        svol = pd.Series(session).map(_SESSION_VOL).to_numpy()

        # Regimes: stretches of 2-6 days that trend up, trend down or chop. A trend adds roughly half a
        # day's typical move per day, enough for breakouts and pullbacks to exist without running away.
        sigma = vol / np.sqrt(252 * 23 * 60)
        day = idx.normalize()
        days = day.unique()
        lengths = rng.integers(2, 7, size=len(days))
        per_regime = rng.choice([-1.0, 0.0, 0.0, 1.0], size=len(days)) * sigma * np.sqrt(23 * 60) * 0.5 / (23 * 60)
        drift_by_day = np.repeat(per_regime, lengths)[: len(days)]
        drift = drift_by_day[days.get_indexer(day)]
        rets = drift + rng.standard_t(df=4, size=len(idx)) / np.sqrt(2) * sigma * svol
        under = price0 * np.exp(np.cumsum(rets))

        rows = []
        for i, (symbol, exp) in enumerate(_contracts(root, start, end)):
            live = (idx < exp) & (idx > exp - pd.Timedelta(days=200))
            if not live.any():
                continue
            dte = ((exp - idx[live]).total_seconds() / 86400).to_numpy()
            roll_date = exp - pd.Timedelta(days=8)
            # Most volume trades in the front month until ~8 days before expiry, then moves to the next one.
            is_front = (idx[live] < roll_date) & (idx[live] >= (_prev_roll(root, start, end, symbol)))
            share = np.where(is_front, 0.93, np.where(idx[live] >= roll_date, 0.04, 0.03))
            base_volume = pd.Series(session[live]).map(_SESSION_VOLUME).to_numpy() * share
            volume = rng.poisson(base_volume)
            keep = volume > 0
            close = under[live] * (1 + carry * dte / 365)
            noise = np.abs(rng.normal(0, sigma * 0.6, size=live.sum())) * close * svol[live]
            open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, sigma * 0.1, size=live.sum()))
            high = np.maximum(open_, close) + noise
            low = np.minimum(open_, close) - noise * rng.uniform(0.5, 1.0, size=live.sum())
            tick = sp.tick_size
            df = pd.DataFrame(
                {
                    "ts": idx[live][keep],
                    "symbol": symbol,
                    "open": np.round(open_[keep] / tick) * tick,
                    "high": np.ceil(high[keep] / tick) * tick,
                    "low": np.floor(low[keep] / tick) * tick,
                    "close": np.round(close[keep] / tick) * tick,
                    "volume": volume[keep].astype("int64"),
                }
            )
            df["high"] = df[["open", "high", "close"]].max(axis=1)
            df["low"] = df[["open", "low", "close"]].min(axis=1)
            rows.append(df)
        if not rows:
            return empty_bars()
        out = pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"]).reset_index(drop=True)
        for c in ("open", "high", "low", "close"):
            out[c] = out[c].round(10)
        return out[BAR_COLUMNS]


def _prev_roll(root: str, start: pd.Timestamp, end: pd.Timestamp, symbol: str) -> pd.Timestamp:
    """When `symbol` became the front month: the roll date of the contract before it."""
    cs = _contracts(root, start, end)
    names = [c[0] for c in cs]
    i = names.index(symbol)
    if i == 0:
        return pd.Timestamp.min.tz_localize("UTC")
    return cs[i - 1][1] - pd.Timedelta(days=8)
