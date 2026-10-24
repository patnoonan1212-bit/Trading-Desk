import pandas as pd

from tests.conftest import raw_bars
from tradingdesk.data.validate import clean_bars, find_gaps


def _series(n=200, start="2024-03-05 14:30", price=5000.0):
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return [(t, "MESH4", price + (i % 4) * 0.25, price + 1, price - 1, price + (i % 3) * 0.25, 100) for i, t in enumerate(ts)]


def test_clean_data_passes():
    df, rep = clean_bars(raw_bars(_series()), "MES")
    assert rep.issues == []
    assert len(df) == 200


def test_problems_are_fixed_and_reported():
    rows = _series()
    rows.append(rows[10])                                                         # duplicate
    rows[20] = (rows[20][0], "MESH4", 5000, 4990, 5001, 5000, 100)                 # high below low
    rows[30] = (rows[30][0], "MESH4", 0, 0, 0, 0, 100)                             # zero price
    rows[40] = (rows[40][0], "MESH4", 5000, 5000.1, 5000, 5000, 100)               # off the tick grid
    t = rows[100][0]
    rows[100] = (t, "MESH4", 5000, 5200, 5000, 5200, 100)                          # bad print that reverses
    df, rep = clean_bars(raw_bars(rows), "MES")
    checks = {i.check for i in rep.issues}
    assert {"duplicates", "ohlc", "nonpositive", "off_tick", "spike"} <= checks
    assert t not in set(df["ts"])
    assert not df.duplicated(["symbol", "ts"]).any()
    assert len(df) == 200 - 3  # bad high/low, zero price and the spike removed; duplicate collapsed


def _cont(index):
    return pd.DataFrame({"close": 1.0}, index=pd.DatetimeIndex(index))


def test_gap_during_regular_hours_is_flagged():
    a = pd.date_range("2024-03-05 14:30", periods=10, freq="1min", tz="UTC")
    b = pd.date_range("2024-03-05 15:30", periods=10, freq="1min", tz="UTC")  # 50-minute hole at ~10:40 ET
    gaps = find_gaps(_cont(a.append(b)), "MES")
    assert len(gaps) == 1


def test_daily_break_weekend_and_holiday_are_not_gaps():
    # 16:59 ET -> 18:00 ET (daily break)
    brk = pd.DatetimeIndex(["2024-03-05 21:59", "2024-03-05 23:00"], tz="UTC")  # 16:59 -> 18:00 ET (winter time)
    assert find_gaps(_cont(brk), "MES").empty
    # Friday 16:59 ET -> Sunday 18:00 ET
    wknd = pd.DatetimeIndex(["2024-03-08 21:59", "2024-03-10 22:00"], tz="UTC")
    assert find_gaps(_cont(wknd), "MES").empty
    # Thanksgiving 2024: equity futures close early at 13:00 ET and reopen 18:00 ET
    tg = pd.DatetimeIndex(["2024-11-28 17:59", "2024-11-28 23:00"], tz="UTC")
    assert find_gaps(_cont(tg), "MES").empty
