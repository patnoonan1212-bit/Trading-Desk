"""Market Regime Agent: what kind of market is each contract in right now?

Trend, from completed 1-hour bars:
  trending_up     ADX above `trend_adx` (a clear trend) and price above a rising EMA
  trending_down   ADX above `trend_adx` and price below a falling EMA
  ranging         ADX below `range_adx` (no real direction, choppy)
  transition      anything in between (a trend starting or fading)
Volatility: where the current ATR (as a % of price) sits among the last ~2 weeks of 1-hour bars:
  high (top 20%), low (bottom 20%), normal otherwise.

It then tells the Scanner which setups are allowed, using the `regime.allowed` table in the settings.
Each setup bar is matched with the latest 1-hour reading that had FINISHED by the time that setup bar closed,
so the regime never uses information from the future.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingdesk.config import Regime
from tradingdesk.data.resample import resample
from tradingdesk.indicators import adx, atr, ema, rolling_percentile

TRENDS = ("trending_up", "trending_down", "ranging", "transition")

_WORDS = {
    "trending_up": "trending up",
    "trending_down": "trending down",
    "ranging": "ranging (choppy, no clear direction)",
    "transition": "in transition (a trend starting or fading)",
}


def describe(trend: str, volatility: str) -> str:
    return f"{_WORDS.get(trend, trend)} with {volatility} volatility"


class RegimeAgent:
    name = "regime"

    def __init__(self, cfg: Regime) -> None:
        self.cfg = cfg

    def classify(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Regime for each completed bar (normally 1-hour bars). Needs `end` (completion time) like resample() gives."""
        c = self.cfg
        out = pd.DataFrame(index=bars.index)
        out["end"] = bars["end"]
        out["adx"] = adx(bars, c.adx_period)
        e = ema(bars["close"], c.ema_period)
        slope = e.diff(3)
        above, below = bars["close"] > e, bars["close"] < e
        trend = np.select(
            [
                (out["adx"] >= c.trend_adx) & above & (slope > 0),
                (out["adx"] >= c.trend_adx) & below & (slope < 0),
                out["adx"] < c.range_adx,
            ],
            ["trending_up", "trending_down", "ranging"],
            default="transition",
        )
        out["trend"] = np.where(out["adx"].isna(), "unknown", trend)
        vol = atr(bars, 14) / bars["close"].abs().clip(lower=1e-9)
        out["vol_pct"] = rolling_percentile(vol, c.vol_lookback_bars)
        out["volatility"] = np.select(
            [out["vol_pct"] >= c.high_vol_pct, out["vol_pct"] <= c.low_vol_pct], ["high", "low"], default="normal"
        )
        out.loc[out["vol_pct"].isna(), "volatility"] = "unknown"
        return out

    def for_continuous(self, cont: pd.DataFrame) -> pd.DataFrame:
        return self.classify(resample(cont, self.cfg.timeframe))

    @staticmethod
    def align(regimes: pd.DataFrame, setup_bars: pd.DataFrame) -> pd.DataFrame:
        """Attach to each setup bar the latest regime reading completed by the time that setup bar completed."""
        left = pd.DataFrame({"end": pd.to_datetime(setup_bars["end"])}, index=setup_bars.index).reset_index(names="_ts")
        right = regimes[["end", "trend", "volatility", "adx"]].rename(columns={"end": "regime_end"}).sort_values("regime_end")
        left["end"] = left["end"].astype(right["regime_end"].dtype)
        merged = pd.merge_asof(left.sort_values("end"), right, left_on="end", right_on="regime_end", direction="backward")
        merged = merged.set_index("_ts").reindex(setup_bars.index)
        out = setup_bars.copy()
        out["trend"] = merged["trend"].fillna("unknown").to_numpy()
        out["volatility"] = merged["volatility"].fillna("unknown").to_numpy()
        out["regime_adx"] = merged["adx"].to_numpy()
        return out

    def allows(self, setup: str, trend: str, volatility: str) -> tuple[bool, str]:
        """Is `setup` allowed right now? Returns (allowed, plain-English reason)."""
        if trend == "unknown" or volatility == "unknown":
            return False, "not enough history yet to read the market"
        allowed = self.cfg.allowed.get(setup)
        if allowed is not None and trend not in allowed:
            return False, f"{setup.replace('_', ' ')} setups aren't used when the market is {_WORDS.get(trend, trend)}"
        if volatility == "high" and setup in self.cfg.blocked_in_high_volatility:
            return False, f"{setup.replace('_', ' ')} setups aren't used when volatility is high"
        return True, f"market is {describe(trend, volatility)}"
