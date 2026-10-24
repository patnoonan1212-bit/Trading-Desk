"""Market Scanner Agent: finds trade setups and turns them into scored trade candidates.

Nine setups, all on completed setup-timeframe bars (15 minutes by default):

  trend_breakout    close beyond the last N bars' high/low on above-average volume, not against the trend
  pullback          in a trend, price dips to the fast EMA and a reversal bar closes beyond the prior bar
  mean_reversion    in a range, price closes back inside the Bollinger band after closing outside it
  liquidity_sweep   price runs the stops beyond a recent high/low by a little, then closes back inside
  squeeze_breakout  volatility compressed to an unusually narrow band, then a high-volume break out of it

Four more built on the levels intraday futures traders watch (session times in New York time, per contract):
  opening_range     the first 30 minutes of the main session set a range; a close beyond it on volume
  prior_day_level   a close through yesterday's high or low on volume, holding beyond it
  overnight_reject  price pokes past the overnight high/low during the main session, then closes back inside
  vwap_reclaim      in a trend, price dips through the day's volume-weighted average price, then closes back over it

Every candidate gets: direction, entry, an entry zone, a stop beyond real structure plus an ATR buffer, a first
partial-profit level (2R) and a structural target, its reward-to-risk, a 0-100 score and a plain-English reason.
Targets come from the market's structure (the other side of a range, a measured move of the last swing, or
several ATRs for breakouts), so the 3:1 filter means something. Candidates below 3:1 or below the minimum score,
or not allowed by the Regime Agent, are dropped with the reason logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingdesk.agents.regime import RegimeAgent, describe
from tradingdesk.backtest.models import TradePlan
from tradingdesk.backtest.strategy import Context, Strategy
from tradingdesk.config import Settings
from tradingdesk.indicators import atr, bollinger, ema, prior_high, prior_low, rolling_percentile
from tradingdesk.timeutil import ET, session_of, trading_date

LEVEL_SETUPS = ("opening_range", "prior_day_level", "overnight_reject", "vwap_reclaim")
# When each contract's main (pit-era) session opens, New York time.
SESSION_OPEN = {"MES": "09:30", "MNQ": "09:30", "MCL": "09:00", "MGC": "08:20"}


def session_levels(b: pd.DataFrame, symbol: str, or_minutes: int = 30) -> pd.DataFrame:
    """Per bar, using only bars already finished: yesterday's high/low, the overnight range, the opening range,
    the day's high/low so far and the day's VWAP. Levels are NaN until they are known."""
    idx = pd.DatetimeIndex(b.index)
    et = idx.tz_convert(ET)
    minute = et.hour * 60 + et.minute
    h, m = map(int, SESSION_OPEN.get(symbol, "09:30").split(":"))
    open_min = h * 60 + m
    td = pd.Series(trading_date(idx), index=b.index)
    # The Globex day runs 18:00 -> 17:00 ET, so "before the open" = evening and early morning of that trade date.
    pre_open = (minute < open_min) | (minute >= 18 * 60)
    in_or = (minute >= open_min) & (minute < open_min + or_minutes)
    after_open = (minute >= open_min) & (minute < 17 * 60)
    after_or = (minute >= open_min + or_minutes) & (minute < 17 * 60)
    out = pd.DataFrame(index=b.index)
    hi, lo = b["high"], b["low"]
    day_hi, day_lo = hi.groupby(td).max(), lo.groupby(td).min()
    out["pd_high"] = td.map(day_hi.shift(1)).to_numpy(dtype=float)   # the previous trading day's high and low
    out["pd_low"] = td.map(day_lo.shift(1)).to_numpy(dtype=float)
    out["on_high"] = hi.where(pre_open).groupby(td).transform("max").where(after_open)
    out["on_low"] = lo.where(pre_open).groupby(td).transform("min").where(after_open)
    out["or_high"] = hi.where(in_or).groupby(td).transform("max").where(after_or)
    out["or_low"] = lo.where(in_or).groupby(td).transform("min").where(after_or)
    out["day_high"] = hi.groupby(td).cummax()
    out["day_low"] = lo.groupby(td).cummin()
    typical = (b["high"] + b["low"] + b["close"]) / 3
    pv = (typical * b["volume"]).groupby(td).cumsum()
    vv = b["volume"].groupby(td).cumsum()
    out["vwap"] = (pv / vv.where(vv > 0)).where(after_open)
    out["mins_after_open"] = np.where(after_open, minute - open_min, np.nan)
    return out


_RESAMPLE_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _coarser(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate setup bars into coarser bars (e.g. 15m -> 1h) with completion times."""
    out = bars[list(_RESAMPLE_AGG)].resample(rule, label="left", closed="left").agg(_RESAMPLE_AGG).dropna(subset=["open"])
    last_end = bars["end"].resample(rule, label="left", closed="left").max()
    out["end"] = last_end.reindex(out.index)
    return out


class _Row:
    """A cheap view of one bar: r["close"], r.get("atr"). Much faster than DataFrame.iloc in a loop."""

    __slots__ = ("_c", "_i")

    def __init__(self, cols: dict, i: int) -> None:
        self._c, self._i = cols, i

    def __getitem__(self, name: str):
        return self._c[name][self._i]

    def get(self, name: str, default=None):
        col = self._c.get(name)
        return default if col is None else col[self._i]


@dataclass
class Candidate:
    setup: str
    direction: int
    entry: float
    stop: float
    target: float
    score: float
    why: str
    confirm_level: float | None
    zone: tuple[float, float] = (0.0, 0.0)
    details: dict = field(default_factory=dict)

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk > 0 else 0.0


class ScannerAgent(Strategy):
    """Implements the Strategy interface, so the same engine runs it in backtests, paper and live."""

    name = "scanner"

    def __init__(self, settings: Settings, setups: dict[str, dict] | None = None) -> None:
        self.s = settings
        self.timeframe = settings.scanner.timeframe
        self.setups = setups if setups is not None else settings.scanner.enabled()
        self.regime = RegimeAgent(settings.regime)
        self.buffer = settings.execution.stops.atr_buffer

    # ----- vectorised preparation (past data only) ------------------------------------------------------
    def prepare(self, symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
        b = bars.copy()
        b["atr"] = atr(b)
        b["vol_avg"] = b["volume"].rolling(20, min_periods=10).mean().shift(1)
        b["vol_ratio"] = b["volume"] / b["vol_avg"]
        for name, p in self.setups.items():
            if name == "trend_breakout":
                n = p.get("lookback", 20)
                b["tb_hh"], b["tb_ll"] = prior_high(b, n), prior_low(b, n)
                b["tb_low5"], b["tb_high5"] = b["low"].rolling(5).min(), b["high"].rolling(5).max()
            elif name == "pullback":
                b["pb_fast"], b["pb_slow"] = ema(b["close"], p.get("fast_ema", 20)), ema(b["close"], p.get("slow_ema", 50))
                k = p.get("swing_lookback", 10)
                b["pb_swing_low"], b["pb_swing_high"] = b["low"].rolling(k).min(), b["high"].rolling(k).max()
                b["pb_leg_high"], b["pb_leg_low"] = b["high"].rolling(40).max(), b["low"].rolling(40).min()
                touched_long = (b["low"] <= b["pb_fast"] + 0.25 * b["atr"]).rolling(3).max().astype(bool)
                touched_short = (b["high"] >= b["pb_fast"] - 0.25 * b["atr"]).rolling(3).max().astype(bool)
                b["pb_touch_long"], b["pb_touch_short"] = touched_long, touched_short
            elif name == "mean_reversion":
                lo, mid, hi = bollinger(b["close"], p.get("band_period", 20), p.get("band_width", 2.0))
                b["mr_lo"], b["mr_mid"], b["mr_hi"] = lo, mid, hi
            elif name == "liquidity_sweep":
                n = p.get("lookback", 30)
                b["ls_hh"], b["ls_ll"] = prior_high(b, n), prior_low(b, n)
            elif name == "squeeze_breakout":
                lo, mid, hi = bollinger(b["close"], p.get("band_period", 20), 2.0)
                b["sq_lo"], b["sq_mid"], b["sq_hi"] = lo, mid, hi
                width = (hi - lo) / mid.abs().clip(lower=1e-9)
                b["sq_rank"] = rolling_percentile(width, 120)
                b["sq_recent"] = (b["sq_rank"] <= p.get("squeeze_pct", 0.10)).rolling(5).max().shift(1).fillna(0).astype(bool)
        if any(n in self.setups for n in LEVEL_SETUPS):
            orm = self.setups.get("opening_range", {}).get("range_minutes", 30)
            b = b.join(session_levels(b, symbol, orm))
            b["sw_low4"], b["sw_high4"] = b["low"].rolling(4).min(), b["high"].rolling(4).max()
        # Regime from completed 1-hour bars, matched without look-ahead.
        regimes = self.regime.classify(_coarser(bars, self.s.regime.timeframe.replace("m", "min")))
        return RegimeAgent.align(regimes, b)

    # ----- per completed bar ----------------------------------------------------------------------------
    def _columns(self, bars: pd.DataFrame) -> dict:
        """Column arrays for fast row access, cached per prepared frame."""
        cache = self.__dict__.setdefault("_cache", {})
        key = id(bars)
        if key not in cache:
            cache[key] = (bars, {c: bars[c].to_numpy() for c in bars.columns})  # keep `bars` alive so its id stays unique
        return cache[key][1]

    def on_bar(self, symbol: str, bars: pd.DataFrame, i: int, ctx: Context) -> list[TradePlan]:
        if i < 2 or ctx.has_trade(symbol):
            return []
        cols = self._columns(bars)
        r, prev = _Row(cols, i), _Row(cols, i - 1)
        a = r["atr"]
        if pd.isna(a) or a <= 0:
            return []
        trend, vol = r["trend"], r["volatility"]
        candidates: list[Candidate] = []
        for name, p in self.setups.items():
            fn = getattr(self, f"_{name}")
            c = fn(r, prev, a, p, trend)
            if c is None:
                continue
            ok, why_regime = self.regime.allows(name, trend, vol)
            if not ok:
                self._note(ctx, symbol, r, "candidate_blocked", f"{name.replace('_', ' ')}: blocked by the Regime Agent, {why_regime}.")
                continue
            c.score = self._score(c, r, trend, vol)
            if c.reward_risk < self.s.risk.min_reward_risk:
                self._note(ctx, symbol, r, "candidate_dropped", f"{name.replace('_', ' ')}: only {c.reward_risk:.1f}:1 potential, below 3:1.")
                continue
            if c.score < self.s.scanner.min_score:
                self._note(ctx, symbol, r, "candidate_dropped", f"{name.replace('_', ' ')}: score {c.score:.0f} is below {self.s.scanner.min_score:.0f}.")
                continue
            c.why = f"{c.why} Market is {describe(trend, vol)}. Score {c.score:.0f}/100, {c.reward_risk:.1f}:1 potential."
            candidates.append(c)
        if not candidates:
            return []
        best = max(candidates, key=lambda c: c.score)
        return [
            TradePlan(symbol, best.direction, float(best.entry), float(best.stop), float(best.target), best.setup, best.why,
                      self.timeframe, confirm_level=best.confirm_level,
                      context={"trend": trend, "volatility": vol, "score": round(best.score, 1)})
        ]

    @staticmethod
    def _note(ctx, symbol, r, action, reason) -> None:
        decide = getattr(ctx, "decide", None)
        if decide:
            decide("scanner", action, reason, symbol, r["end"])

    def _score(self, c: Candidate, r, trend: str, vol: str) -> float:
        """0-100: how good the candidate looks. Simple, transparent parts so the Learning Agent can tune weights later."""
        score = 50.0
        vr = r.get("vol_ratio", np.nan)
        if not pd.isna(vr):
            score += float(np.clip((vr - 1.0) * 15, -10, 15))           # volume behind the move
        with_trend = (trend == "trending_up" and c.direction == 1) or (trend == "trending_down" and c.direction == -1)
        against = (trend == "trending_up" and c.direction == -1) or (trend == "trending_down" and c.direction == 1)
        score += 15 if with_trend else (-15 if against else 0)          # trading with the bigger picture
        score += float(np.clip((c.reward_risk - 3.0) * 4, 0, 10))         # extra room to the target
        session = session_of(pd.Timestamp(r["end"]) - pd.Timedelta(minutes=1))
        score += {"us_regular": 5, "london": 3}.get(session, 0)           # liquid hours
        if vol == "high":
            score -= 5                                                    # wider, less predictable swings
        return float(np.clip(score, 0, 100))

    # ----- the five setups ------------------------------------------------------------------------------
    def _trend_breakout(self, r, prev, a, p, trend) -> Candidate | None:
        hh, ll = r.get("tb_hh"), r.get("tb_ll")
        if pd.isna(hh) or pd.isna(r["vol_ratio"]) or r["vol_ratio"] < p.get("volume_mult", 1.3):
            return None
        height = hh - ll
        if r["close"] > hh and trend != "trending_down":
            stop = r["tb_low5"] - self.buffer * a
            target = r["close"] + max(p.get("target_atr", 6.0) * a, height)
            return Candidate("trend_breakout", 1, r["close"], stop, target, 0, f"15m close {r['close']:.2f} broke above the {p.get('lookback', 20)}-bar high {hh:.2f} on {r['vol_ratio']:.1f}x normal volume.", hh, (hh, r["close"]))
        if r["close"] < ll and trend != "trending_up":
            stop = r["tb_high5"] + self.buffer * a
            target = r["close"] - max(p.get("target_atr", 6.0) * a, height)
            return Candidate("trend_breakout", -1, r["close"], stop, target, 0, f"15m close {r['close']:.2f} broke below the {p.get('lookback', 20)}-bar low {ll:.2f} on {r['vol_ratio']:.1f}x normal volume.", ll, (r["close"], ll))
        return None

    def _pullback(self, r, prev, a, p, trend) -> Candidate | None:
        fast, slow = r.get("pb_fast"), r.get("pb_slow")
        if pd.isna(fast) or pd.isna(slow):
            return None
        if trend == "trending_up" and fast > slow and r["pb_touch_long"] and r["close"] > fast and r["close"] > r["open"] and r["close"] > prev["high"]:
            stop = r["pb_swing_low"] - self.buffer * a
            leg = r["pb_leg_high"] - r["pb_swing_low"]
            target = max(r["pb_leg_high"] + leg, r["close"] + 2 * leg)
            return Candidate("pullback", 1, r["close"], stop, target, 0, f"Uptrend pulled back to the {p.get('fast_ema', 20)} EMA ({fast:.2f}) and a 15m bar closed back above the prior high {prev['high']:.2f}.", r["high"], (fast, r["close"]))
        if trend == "trending_down" and fast < slow and r["pb_touch_short"] and r["close"] < fast and r["close"] < r["open"] and r["close"] < prev["low"]:
            stop = r["pb_swing_high"] + self.buffer * a
            leg = r["pb_swing_high"] - r["pb_leg_low"]
            target = min(r["pb_leg_low"] - leg, r["close"] - 2 * leg)
            return Candidate("pullback", -1, r["close"], stop, target, 0, f"Downtrend bounced to the {p.get('fast_ema', 20)} EMA ({fast:.2f}) and a 15m bar closed back below the prior low {prev['low']:.2f}.", r["low"], (r["close"], fast))
        return None

    def _mean_reversion(self, r, prev, a, p, trend) -> Candidate | None:
        lo, hi, plo, phi = r.get("mr_lo"), r.get("mr_hi"), prev.get("mr_lo"), prev.get("mr_hi")
        if pd.isna(lo) or pd.isna(plo):
            return None
        if prev["close"] < plo and r["close"] > lo:
            stop = min(r["low"], prev["low"]) - self.buffer * a
            return Candidate("mean_reversion", 1, r["close"], stop, hi, 0, f"Price closed back inside the lower band ({lo:.2f}) after dipping below it; aiming for the top of the range ({hi:.2f}).", r["high"], (lo, r["close"]))
        if prev["close"] > phi and r["close"] < hi:
            stop = max(r["high"], prev["high"]) + self.buffer * a
            return Candidate("mean_reversion", -1, r["close"], stop, lo, 0, f"Price closed back inside the upper band ({hi:.2f}) after poking above it; aiming for the bottom of the range ({lo:.2f}).", r["low"], (r["close"], hi))
        return None

    def _liquidity_sweep(self, r, prev, a, p, trend) -> Candidate | None:
        hh, ll = r.get("ls_hh"), r.get("ls_ll")
        if pd.isna(hh):
            return None
        max_run = p.get("max_sweep_atr", 0.75) * a
        mid = (r["high"] + r["low"]) / 2
        if ll - max_run < r["low"] < ll and r["close"] > ll and r["close"] > mid:
            stop = r["low"] - self.buffer * a
            return Candidate("liquidity_sweep", 1, r["close"], stop, hh, 0, f"Price dipped below the recent low {ll:.2f} to {r['low']:.2f}, likely triggering stops, then closed back above it.", r["close"], (ll, r["close"]))
        if hh < r["high"] < hh + max_run and r["close"] < hh and r["close"] < mid:
            stop = r["high"] + self.buffer * a
            return Candidate("liquidity_sweep", -1, r["close"], stop, ll, 0, f"Price spiked above the recent high {hh:.2f} to {r['high']:.2f}, likely triggering stops, then closed back below it.", r["close"], (r["close"], hh))
        return None

    def _squeeze_breakout(self, r, prev, a, p, trend) -> Candidate | None:
        hi, lo, mid = r.get("sq_hi"), r.get("sq_lo"), r.get("sq_mid")
        if pd.isna(hi) or not r.get("sq_recent", False) or pd.isna(r["vol_ratio"]) or r["vol_ratio"] < p.get("volume_mult", 1.5):
            return None
        if r["close"] > hi and trend != "trending_down":
            stop = min(mid, r["low"]) - self.buffer * a
            return Candidate("squeeze_breakout", 1, r["close"], stop, r["close"] + p.get("target_atr", 6.0) * a, 0, f"After an unusually quiet stretch, price broke above the band ({hi:.2f}) on {r['vol_ratio']:.1f}x normal volume.", hi, (hi, r["close"]))
        if r["close"] < lo and trend != "trending_up":
            stop = max(mid, r["high"]) + self.buffer * a
            return Candidate("squeeze_breakout", -1, r["close"], stop, r["close"] - p.get("target_atr", 6.0) * a, 0, f"After an unusually quiet stretch, price broke below the band ({lo:.2f}) on {r['vol_ratio']:.1f}x normal volume.", lo, (r["close"], lo))
        return None

    # ----- levels futures traders watch -------------------------------------------------------------------
    def _opening_range(self, r, prev, a, p, trend) -> Candidate | None:
        orh, orl, mins = r.get("or_high"), r.get("or_low"), r.get("mins_after_open")
        if pd.isna(orh) or pd.isna(mins) or mins > p.get("last_entry_minutes", 150) or pd.isna(r["vol_ratio"]) \
                or r["vol_ratio"] < p.get("volume_mult", 1.2):
            return None
        height, mid = orh - orl, (orh + orl) / 2
        if height <= 0:
            return None
        if r["close"] > orh and prev["close"] <= orh and trend != "trending_down":
            return Candidate("opening_range", 1, r["close"], mid - self.buffer * a, r["close"] + p.get("target_ranges", 2.0) * height, 0,
                             f"15m close {r['close']:.2f} broke above the opening range ({orl:.2f}-{orh:.2f}) on {r['vol_ratio']:.1f}x normal volume.",
                             orh, (orh, r["close"]))
        if r["close"] < orl and prev["close"] >= orl and trend != "trending_up":
            return Candidate("opening_range", -1, r["close"], mid + self.buffer * a, r["close"] - p.get("target_ranges", 2.0) * height, 0,
                             f"15m close {r['close']:.2f} broke below the opening range ({orl:.2f}-{orh:.2f}) on {r['vol_ratio']:.1f}x normal volume.",
                             orl, (r["close"], orl))
        return None

    def _prior_day_level(self, r, prev, a, p, trend) -> Candidate | None:
        pdh, pdl = r.get("pd_high"), r.get("pd_low")
        if pd.isna(pdh) or pd.isna(r.get("mins_after_open")) or pd.isna(r["vol_ratio"]) or r["vol_ratio"] < p.get("volume_mult", 1.3):
            return None
        rng = pdh - pdl
        if r["close"] > pdh and prev["close"] <= pdh and r["low"] < pdh + 0.5 * a and trend != "trending_down":
            stop = min(r["low"], pdh) - self.buffer * a
            return Candidate("prior_day_level", 1, r["close"], stop, r["close"] + max(p.get("target_atr", 6.0) * a, 0.5 * rng), 0,
                             f"15m close {r['close']:.2f} broke above yesterday's high {pdh:.2f} on {r['vol_ratio']:.1f}x normal volume.",
                             pdh, (pdh, r["close"]))
        if r["close"] < pdl and prev["close"] >= pdl and r["high"] > pdl - 0.5 * a and trend != "trending_up":
            stop = max(r["high"], pdl) + self.buffer * a
            return Candidate("prior_day_level", -1, r["close"], stop, r["close"] - max(p.get("target_atr", 6.0) * a, 0.5 * rng), 0,
                             f"15m close {r['close']:.2f} broke below yesterday's low {pdl:.2f} on {r['vol_ratio']:.1f}x normal volume.",
                             pdl, (r["close"], pdl))
        return None

    def _overnight_reject(self, r, prev, a, p, trend) -> Candidate | None:
        onh, onl, mins = r.get("on_high"), r.get("on_low"), r.get("mins_after_open")
        if pd.isna(onh) or pd.isna(mins) or mins > p.get("last_entry_minutes", 240):
            return None
        max_run = p.get("max_poke_atr", 0.75) * a
        if onh < r["high"] < onh + max_run and r["close"] < onh and r["close"] < (r["high"] + r["low"]) / 2:
            return Candidate("overnight_reject", -1, r["close"], r["high"] + self.buffer * a, onl, 0,
                             f"Price poked above the overnight high {onh:.2f} to {r['high']:.2f}, then closed back below it; "
                             f"aiming for the overnight low {onl:.2f}.", r["close"], (r["close"], onh))
        if onl - max_run < r["low"] < onl and r["close"] > onl and r["close"] > (r["high"] + r["low"]) / 2:
            return Candidate("overnight_reject", 1, r["close"], r["low"] - self.buffer * a, onh, 0,
                             f"Price dipped below the overnight low {onl:.2f} to {r['low']:.2f}, then closed back above it; "
                             f"aiming for the overnight high {onh:.2f}.", r["close"], (onl, r["close"]))
        return None

    def _vwap_reclaim(self, r, prev, a, p, trend) -> Candidate | None:
        vw, pvw = r.get("vwap"), prev.get("vwap")
        if pd.isna(vw) or pd.isna(pvw) or pd.isna(r.get("sw_low4")):
            return None
        if trend == "trending_up" and prev["close"] < pvw and r["close"] > vw and r["close"] > r["open"]:
            stop = r["sw_low4"] - self.buffer * a
            levels = [x for x in (r.get("day_high"), r.get("pd_high"), r.get("on_high")) if not pd.isna(x) and x > r["close"]]
            target = max(levels + [r["close"] + p.get("target_atr", 5.0) * a])
            return Candidate("vwap_reclaim", 1, r["close"], stop, target, 0,
                             f"In an uptrend, price dipped below the day's average price (VWAP {vw:.2f}) and a 15m bar closed back above it.",
                             r["high"], (vw, r["close"]))
        if trend == "trending_down" and prev["close"] > pvw and r["close"] < vw and r["close"] < r["open"]:
            stop = r["sw_high4"] + self.buffer * a
            levels = [x for x in (r.get("day_low"), r.get("pd_low"), r.get("on_low")) if not pd.isna(x) and x < r["close"]]
            target = min(levels + [r["close"] - p.get("target_atr", 5.0) * a])
            return Candidate("vwap_reclaim", -1, r["close"], stop, target, 0,
                             f"In a downtrend, price bounced above the day's average price (VWAP {vw:.2f}) and a 15m bar closed back below it.",
                             r["low"], (r["close"], vw))
        return None
