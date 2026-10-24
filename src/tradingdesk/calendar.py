"""Scheduled high-impact economic releases, used by the Risk Manager's news blackout.

The calendar lives in a plain CSV file (config/economic_calendar.csv) with columns:
    time_et (YYYY-MM-DD HH:MM, New York time), event, impact (high/medium/low)
In Phase 4 the News & Sentiment Agent keeps it up to date from a live calendar source.

`build_default_calendar()` writes a starter file with:
  - US jobs report (NFP): first Friday of each month at 08:30 ET. This is the usual schedule; the Labor Department
    occasionally moves it (holidays, shutdowns), so check the official schedule before relying on a specific date.
  - FOMC rate decisions at 14:00 ET on the Fed's published meeting dates.
CPI release dates vary and are not generated here; they arrive with the News Agent's calendar in Phase 4.
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


def _first_fridays(start_year: int, end_year: int) -> list[pd.Timestamp]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            d = pd.Timestamp(year=y, month=m, day=1)
            out.append(d + pd.Timedelta(days=(4 - d.weekday()) % 7))
    return out


def build_default_calendar(start_year: int = 2022, end_year: int = 2026) -> pd.DataFrame:
    rows = [(f"{d:%Y-%m-%d} 08:30", "US jobs report (NFP)", "high") for d in _first_fridays(start_year, end_year)]
    rows += [(f"{d} 14:00", "FOMC rate decision", "high") for d in FOMC_DECISION_DATES if start_year <= int(d[:4]) <= end_year]
    return pd.DataFrame(rows, columns=["time_et", "event", "impact"]).sort_values("time_et").reset_index(drop=True)


class EconomicCalendar:
    def __init__(self, events: pd.DataFrame) -> None:
        ev = events[events["impact"].str.lower() == "high"].copy()
        ev["ts"] = pd.to_datetime(ev["time_et"]).dt.tz_localize(ET).dt.tz_convert("UTC")
        ev = ev.sort_values("ts")
        self._ts = list(ev["ts"])
        self._names = list(ev["event"])

    @classmethod
    def load(cls, path: str | Path | None) -> EconomicCalendar:
        if path and Path(path).exists():
            return cls(pd.read_csv(path))
        return cls(build_default_calendar())

    def __len__(self) -> int:
        return len(self._ts)

    def blocking_event(self, ts: pd.Timestamp, minutes: int) -> tuple[str, pd.Timestamp] | None:
        """The high-impact event within `minutes` before or after `ts`, if any."""
        if not self._ts or minutes <= 0:
            return None
        w = pd.Timedelta(minutes=minutes)
        i = bisect.bisect_left(self._ts, ts - w)
        if i < len(self._ts) and self._ts[i] <= ts + w:
            return self._names[i], self._ts[i]
        return None
