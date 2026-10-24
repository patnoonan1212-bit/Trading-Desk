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
