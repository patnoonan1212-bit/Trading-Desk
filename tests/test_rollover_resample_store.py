import pandas as pd

from tests.conftest import raw_bars
from tradingdesk.data.resample import resample
from tradingdesk.data.rollover import build_continuous, roll_schedule
from tradingdesk.data.store import DataStore


def _two_contracts():
    rows = []
    # Three sessions. Day 1: MESH4 busier. Day 2: MESM4 busier. Day 3: both trade.
    for day, (vol_h, vol_m) in zip(["2024-03-11", "2024-03-12", "2024-03-13"], [(900, 100), (100, 900), (50, 950)], strict=True):
        for i, t in enumerate(pd.date_range(f"{day} 14:30", periods=60, freq="1min", tz="UTC")):
            px = 5000 + i * 0.25
            rows.append((t, "MESH4", px, px + 0.5, px - 0.5, px, vol_h))
            rows.append((t, "MESM4", px + 40, px + 40.5, px + 39.5, px + 40, vol_m))
    return raw_bars(rows)


def test_roll_waits_until_volume_moves_and_switches_next_session():
    sched = roll_schedule(_two_contracts(), confirm_days=1)
    assert list(sched.values) == ["MESH4", "MESH4", "MESM4"]  # decided after day 2 closed, used from day 3


def test_continuous_has_no_fake_jump_and_keeps_real_prices():
    cont = build_continuous(_two_contracts(), confirm_days=1)
    roll_at = cont.index[cont["roll"]][0]
    before = cont[cont.index < roll_at]
    after = cont[cont.index >= roll_at]
    assert (before["adjustment"] == 40).all() and (after["adjustment"] == 0).all()
    assert (after["raw_close"] == after["close"]).all()            # latest contract unchanged
    assert (before["close"] - before["raw_close"] == 40).all()       # history shifted by the roll gap
    assert set(after["contract"]) == {"MESM4"}


def test_resample_and_completion_time():
    cont = build_continuous(_two_contracts(), confirm_days=1)
    five = resample(cont, "5m")
    first = cont.iloc[:5]
    row = five.iloc[0]
    assert row["open"] == first["open"].iloc[0] and row["close"] == first["close"].iloc[-1]
    assert row["high"] == first["high"].max() and row["volume"] == first["volume"].sum()
    assert row["end"] - five.index[0] == pd.Timedelta(minutes=5)


def test_daily_bars_follow_cme_trade_date():
    idx = pd.DatetimeIndex(["2024-03-04 23:30", "2024-03-05 15:00", "2024-03-05 23:30"], tz="UTC")  # 18:30 ET counts as the next day
    cont = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0, 2.0, 3.0], "volume": 1.0}, index=idx)
    from tradingdesk.timeutil import trading_date

    cont["trade_date"] = trading_date(idx)
    daily = resample(cont, "1d")
    assert len(daily) == 2
    assert list(daily["close"]) == [2.0, 3.0]


def test_store_merges_without_duplicates(tmp_path):
    store = DataStore(tmp_path)
    df = _two_contracts()
    store.write_bars(df, "MES")
    store.write_bars(df.iloc[:50], "MES")
    back = store.read_bars("MES")
    assert len(back) == len(df)
    part = store.read_bars("MES", "2024-03-12", "2024-03-13")
    assert part["ts"].min() >= pd.Timestamp("2024-03-12", tz="UTC")
    assert store.coverage("MES")[2] == len(df)
