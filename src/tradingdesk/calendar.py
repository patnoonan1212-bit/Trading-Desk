"""Scheduled high-impact economic releases, used by the Risk Manager's news blackout.

The calendar lives in a plain CSV file (config/economic_calendar.csv) with columns:
    time_et (YYYY-MM-DD HH:MM, New York time), event, impact (high/medium/low), symbols (optional)
`symbols` limits an event to certain contracts (e.g. "MCL" for the weekly crude oil inventory report); empty
means it affects everything. During paper trading the News & Sentiment Agent adds this week's events from a live
calendar.

`build_default_calendar()` writes a starter file with:
  - US jobs report (NFP) at 08:30 ET, using the Labor Department's rule: the third Friday after the week that
    contains the 12th of the previous month. That is often, but not always, the first Friday. Holidays and
    government shutdowns occasionally move it, so check the official schedule before relying on a specific date.
  - FOMC rate decisions at 14:00 ET on the Fed's published meeting dates.
  - CPI (inflation) at 08:30 ET on the published release dates for 2022 to September 2025. Later dates come from
    the live calendar.
"""

from __future__ import annotations

import bisect
from pathlib import Path

import pandas as pd

from tradingdesk.timeutil import ET

# Second day of each scheduled FOMC meeting (statement at 14:00 ET), from the Federal Reserve's published calendars.
FOMC_DECISION_DATES = [
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


# Consumer Price Index release dates (08:30 ET), from the Bureau of Labor Statistics schedules.
CPI_RELEASE_DATES = [
    "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12", "2022-05-11", "2022-06-10",
    "2022-07-13", "2022-08-10", "2022-09-13", "2022-10-13", "2022-11-10", "2022-12-13",
    "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12", "2023-05-10", "2023-06-13",
    "2023-07-12", "2023-08-10", "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12",
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12",
    "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13", "2025-06-11",
    "2025-07-15", "2025-08-12", "2025-09-11",
]


def jobs_report_dates(start_year: int, end_year: int) -> list[pd.Timestamp]:
    """Release dates of the monthly jobs report: third Friday after the week (Sun-Sat) containing the 12th."""
    out = []
    for y in range(start_year - 1, end_year + 1):
        for m in range(1, 13):
            twelfth = pd.Timestamp(year=y, month=m, day=12)
            week_end = twelfth + pd.Timedelta(days=(5 - twelfth.weekday()) % 7)  # that week's Saturday
            release = week_end + pd.Timedelta(days=6 + 14)                       # first Friday after, plus two weeks
            if start_year <= release.year <= end_year:
                out.append(release)
    return out


def build_default_calendar(start_year: int = 2022, end_year: int = 2026) -> pd.DataFrame:
    rows = [(f"{d:%Y-%m-%d} 08:30", "US jobs report (NFP)", "high") for d in jobs_report_dates(start_year, end_year)]
    rows += [(f"{d} 14:00", "FOMC rate decision", "high") for d in FOMC_DECISION_DATES if start_year <= int(d[:4]) <= end_year]
    rows += [(f"{d} 08:30", "US inflation (CPI)", "high") for d in CPI_RELEASE_DATES if start_year <= int(d[:4]) <= end_year]
    df = pd.DataFrame(rows, columns=["time_et", "event", "impact"]).assign(symbols="")
    return df.sort_values("time_et").reset_index(drop=True)


class EconomicCalendar:
    def __init__(self, events: pd.DataFrame) -> None:
        ev = events[events["impact"].str.lower() == "high"].copy()
        if "symbols" not in ev:
            ev["symbols"] = ""
        ev["symbols"] = ev["symbols"].fillna("").astype(str)
        ev["ts"] = pd.to_datetime(ev["time_et"]).dt.tz_localize(ET, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
        ev = ev.dropna(subset=["ts"]).drop_duplicates(["ts", "event"]).sort_values("ts")
        self.events = ev[["ts", "event", "symbols"]].reset_index(drop=True)
        self._ts = list(ev["ts"])
        self._names = list(ev["event"])
        self._symbols = [set(x.split()) for x in ev["symbols"]]

    @classmethod
    def load(cls, path: str | Path | None, extra: pd.DataFrame | None = None) -> EconomicCalendar:
        base = pd.read_csv(path, keep_default_na=False) if path and Path(path).exists() else build_default_calendar()
        return cls(base if extra is None or extra.empty else pd.concat([base, extra], ignore_index=True))

    def __len__(self) -> int:
        return len(self._ts)

    def blocking_event(self, ts: pd.Timestamp, minutes: int, symbol: str | None = None) -> tuple[str, pd.Timestamp] | None:
        """The high-impact event affecting `symbol` within `minutes` before or after `ts`, if any."""
        if not self._ts or minutes <= 0:
            return None
        w = pd.Timedelta(minutes=minutes)
        i = bisect.bisect_left(self._ts, ts - w)
        while i < len(self._ts) and self._ts[i] <= ts + w:
            if not self._symbols[i] or symbol is None or symbol in self._symbols[i]:
                return self._names[i], self._ts[i]
            i += 1
        return None

    def upcoming(self, ts: pd.Timestamp, hours: float, symbol: str | None = None) -> list[tuple[pd.Timestamp, str]]:
        """High-impact events in the next `hours` that affect `symbol`."""
        i = bisect.bisect_left(self._ts, ts)
        out = []
        while i < len(self._ts) and self._ts[i] <= ts + pd.Timedelta(hours=hours):
            if not self._symbols[i] or symbol is None or symbol in self._symbols[i]:
                out.append((self._ts[i], self._names[i]))
            i += 1
        return out
