"""Data quality checks and cleaning.

Each check either fixes the data (and says what it removed) or flags something a human or the Watchdog
should know about. Nothing is silently changed: every fix and flag becomes a DataQualityIssue.

Checks on raw 1-minute bars, per contract:
  duplicates     two bars for the same contract and minute (keep the higher-volume one)
  ohlc           high below open/close, low above open/close, or high < low (bar removed)
  nonpositive    zero or negative prices (removed; negative crude is possible but flagged critical)
  volume         missing or negative volume (removed)
  off_tick       prices that are not a multiple of the tick size (flagged)
  spike          a one-bar jump that immediately reverses, typical of a bad print (removed)
Checks on the continuous series:
  gap            no data for a long stretch while the market should be open (flagged)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingdesk.contracts import spec
from tradingdesk.core.events import DataQualityIssue
from tradingdesk.timeutil import ET

# Gaps longer than this (minutes) are flagged. Overnight micros can go several minutes without a trade.
GAP_MINUTES = {"us_regular": 15, "london": 45, "overnight": 90}


@dataclass
class ValidationReport:
    root: str
    rows_in: int = 0
    rows_out: int = 0
    issues: list[DataQualityIssue] = field(default_factory=list)

    def add(self, check: str, severity: str, message: str, ts: pd.Series | pd.Index | None = None, count: int | None = None) -> None:
        n = count if count is not None else (len(ts) if ts is not None else 1)
        if n == 0:
            return
        first = last = None
        if ts is not None and len(ts):
            first, last = pd.Timestamp(min(ts)).to_pydatetime(), pd.Timestamp(max(ts)).to_pydatetime()
        self.issues.append(DataQualityIssue(symbol=self.root, severity=severity, check=check, message=message, count=n, first_ts=first, last_ts=last))

    @property
    def critical(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "critical"]

    def summary(self) -> str:
        if not self.issues:
            return f"{self.root}: {self.rows_in:,} bars checked, no problems found."
        lines = [f"{self.root}: {self.rows_in:,} bars checked, {self.rows_in - self.rows_out:,} removed."]
        lines += [f"  [{i.severity}] {i.check}: {i.message}" for i in self.issues]
        return "\n".join(lines)


def clean_bars(df: pd.DataFrame, root: str) -> tuple[pd.DataFrame, ValidationReport]:
    """Validate and clean raw outright 1-minute bars for one product."""
    rep = ValidationReport(root=root, rows_in=len(df))
    sp = spec(root)
    df = df.copy()

    # Missing or negative volume
    bad_vol = df["volume"].isna() | (df["volume"] < 0)
    rep.add("volume", "warning", f"{bad_vol.sum()} bars with missing or negative volume removed", df.loc[bad_vol, "ts"])
    df = df[~bad_vol]

    # Duplicates: keep the bar with the most volume
    dup = df.duplicated(["symbol", "ts"], keep=False)
    if dup.any():
        n_extra = int(dup.sum() - df[dup].drop_duplicates(["symbol", "ts"]).shape[0])
        rep.add("duplicates", "warning", f"{n_extra} duplicate bars removed (kept the higher-volume copy)", df.loc[dup, "ts"], n_extra)
        df = df.sort_values("volume").drop_duplicates(["symbol", "ts"], keep="last")

    # Non-positive prices
    prices = df[["open", "high", "low", "close"]]
    nonpos = (prices <= 0).any(axis=1)
    rep.add("nonpositive", "critical", f"{nonpos.sum()} bars with zero or negative prices removed", df.loc[nonpos, "ts"])
    df = df[~nonpos]

    # OHLC consistency
    o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
    bad = (h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (h < lo)
    rep.add("ohlc", "warning", f"{bad.sum()} bars with impossible high/low values removed", df.loc[bad, "ts"])
    df = df[~bad]

    # Off the tick grid
    ratio = df[["open", "high", "low", "close"]] / sp.tick_size
    off = (np.abs(ratio - ratio.round()) > 1e-6).any(axis=1)
    rep.add("off_tick", "warning", f"{off.sum()} bars with prices off the {sp.tick_size} tick grid", df.loc[off, "ts"])

    # Isolated spikes: a big jump that reverses on the next bar
    df = df.sort_values(["symbol", "ts"])
    keep = pd.Series(True, index=df.index)
    for _, g in df.groupby("symbol", sort=False):
        if len(g) < 50:
            continue
        ret = g["close"].diff()
        mad = ret.abs().rolling(500, min_periods=50).median().bfill()
        threshold = np.maximum(12 * mad, 20 * sp.tick_size)
        nxt = ret.shift(-1)
        spike = (ret.abs() > threshold) & (np.sign(nxt) == -np.sign(ret)) & (nxt.abs() > 0.7 * ret.abs())
        keep[g.index[spike.to_numpy()]] = False
    rep.add("spike", "warning", f"{(~keep).sum()} one-bar spikes that immediately reversed removed", df.loc[~keep, "ts"])
    df = df[keep]

    df = df.sort_values(["ts", "symbol"]).reset_index(drop=True)
    rep.rows_out = len(df)
    return df, rep


def trading_schedule(root: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Exchange open/close times (UTC) per trade date, including holidays and early closes.

    Falls back to the regular Globex week (Sun 18:00 to Fri 17:00 ET, daily 17:00-18:00 break) if the
    calendar library has no data.
    """
    try:
        import pandas_market_calendars as mcal

        sched = mcal.get_calendar(spec(root).calendar).schedule(start.date(), (end + pd.Timedelta(days=1)).date())
        return sched[["market_open", "market_close"]].rename(columns={"market_open": "open", "market_close": "close"})
    except Exception:
        days = [d for d in pd.date_range(start.date(), (end + pd.Timedelta(days=1)).date()) if d.weekday() < 5]
        opens = [(pd.Timestamp(d) - pd.Timedelta(hours=6)).tz_localize(ET).tz_convert("UTC") for d in days]
        closes = [(pd.Timestamp(d) + pd.Timedelta(hours=17)).tz_localize(ET).tz_convert("UTC") for d in days]
        return pd.DataFrame({"open": opens, "close": closes}, index=days)


def find_gaps(cont: pd.DataFrame, root: str, rep: ValidationReport | None = None) -> pd.DataFrame:
    """Flag long stretches with no bars while the market was scheduled to be open.

    The daily break, weekends, holidays and early closes come from the exchange calendar, so only time the
    market was actually open counts toward a gap.
    """
    rep = rep or ValidationReport(root=root)
    empty = pd.DataFrame(columns=["start", "end", "minutes"])
    if len(cont) < 2:
        return empty
    ts = cont.index if isinstance(cont.index, pd.DatetimeIndex) else pd.DatetimeIndex(cont["ts"])
    prev, nxt = ts[:-1], ts[1:]
    minutes = (nxt - prev).total_seconds() / 60
    cand = minutes > min(GAP_MINUTES.values())
    if not cand.any():
        return empty
    sched = trading_schedule(root, ts[0] - pd.Timedelta(days=1), ts[-1])
    def naive_utc(col: pd.Series) -> np.ndarray:
        return pd.to_datetime(col, utc=True).dt.tz_localize(None).to_numpy("datetime64[ns]")

    opens, closes = naive_utc(sched["open"]), naive_utc(sched["close"])
    rows = []
    for a, b in zip(prev[cand], nxt[cand], strict=True):
        # Minutes inside the gap when the market was scheduled to be open.
        lo = np.maximum(opens, np.datetime64(a.tz_convert("UTC").tz_localize(None) + pd.Timedelta(minutes=1)))
        hi = np.minimum(closes, np.datetime64(b.tz_convert("UTC").tz_localize(None)))
        open_minutes = float(np.clip((hi - lo) / np.timedelta64(1, "m"), 0, None).sum()) if len(opens) else 0.0
        et = a.tz_convert(ET)
        m = et.hour * 60 + et.minute
        sess = "us_regular" if 570 <= m < 960 else "london" if 180 <= m < 570 else "overnight"
        if open_minutes > GAP_MINUTES[sess]:
            rows.append((a, b, open_minutes))
    gaps = pd.DataFrame(rows, columns=["start", "end", "minutes"])
    if len(gaps):
        rep.add("gap", "warning", f"{len(gaps)} stretches with no data while the market was open (longest {int(gaps['minutes'].max())} min)", gaps["start"])
    return gaps
