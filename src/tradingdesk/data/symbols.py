"""Outright contract symbols like 'MESZ5' or 'MESZ25': parsing, ordering and approximate expiry."""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from tradingdesk.contracts import CONTRACTS, MONTH_CODES

_PATTERN = re.compile(r"^(?P<root>[A-Z]+?)(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{1,2})$")


def parse(symbol: str, ref: date | None = None) -> tuple[str, int, int] | None:
    """Return (root, year, month) for an outright, or None for spreads and anything unrecognised.

    Exchanges print one- or two-digit years ('MESZ5'); the decade is the one closest to `ref`.
    """
    m = _PATTERN.match(symbol)
    if not m or m["root"] not in CONTRACTS:
        return None
    month = MONTH_CODES.index(m["month"]) + 1
    digits = m["year"]
    ref_year = (ref or date.today()).year
    if len(digits) == 2:
        year = 2000 + int(digits)
    else:
        candidates = [ref_year - ref_year % 10 + int(digits) + d for d in (-10, 0, 10)]
        year = min(candidates, key=lambda y: abs(y - ref_year))
    return m["root"], year, month


def is_outright(symbol: str) -> bool:
    return parse(symbol) is not None


def sort_key(symbol: str, ref: date | None = None) -> tuple[int, int]:
    p = parse(symbol, ref)
    if p is None:
        raise ValueError(f"Not an outright contract: {symbol}")
    return p[1], p[2]


def third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year=year, month=month, day=1)
    offset = (4 - first.weekday()) % 7
    return first + pd.Timedelta(days=offset + 14)


def approx_last_trade(root: str, year: int, month: int) -> pd.Timestamp:
    """Roughly when a contract stops trading. Used only to pick which months to ask a live feed for.

    Stock index (MES, MNQ): third Friday of the contract month.
    Crude (MCL): around the 19th of the month before. Gold (MGC): around the 26th of the month before.
    """
    if root in ("MCL", "MGC"):
        prev = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
        return prev.replace(day=19 if root == "MCL" else 26)
    return third_friday(year, month)


def active_contracts(root: str, on: pd.Timestamp, n: int = 2) -> list[str]:
    """The next `n` listed contracts that haven't stopped trading by `on`, nearest first (e.g. ['MESZ25', 'MESH26'])."""
    months = CONTRACTS[root].months
    day = pd.Timestamp(on).tz_localize(None).normalize() if pd.Timestamp(on).tz else pd.Timestamp(on).normalize()
    out = []
    year, month = day.year, day.month
    while len(out) < n:
        code = MONTH_CODES[month - 1]
        if code in months and approx_last_trade(root, year, month) >= day:
            out.append(f"{root}{code}{year % 100:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out
