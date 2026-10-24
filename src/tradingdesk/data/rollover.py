"""Contract rollovers and continuous contracts.

Futures expire, so a multi-year backtest has to stitch contract months together. We do it the way traders do:

1. Roll schedule. Each trade date, look at which contract traded the most. When a later contract leads in
   volume for `confirm_days` sessions in a row, switch to it from the NEXT session. Deciding only after a
   session has ended means the backtest never uses information it wouldn't have had.

2. Two price series side by side:
   - raw_open/raw_high/raw_low/raw_close: the actual prices of the contract being traded. Fills and P&L use these.
   - open/high/low/close: back-adjusted. At every roll, all earlier prices are shifted by the price difference
     between the new and old contract, so indicators and charts don't see a fake jump at the roll.
   The adjustment is additive (points), because our stops and ATRs are measured in points.
"""

from __future__ import annotations

import pandas as pd

from tradingdesk.data.symbols import sort_key
from tradingdesk.timeutil import trading_date

CONTINUOUS_COLUMNS = [
    "open", "high", "low", "close",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "volume", "contract", "trade_date", "roll", "adjustment",
]


def roll_schedule(bars: pd.DataFrame, confirm_days: int = 1) -> pd.Series:
    """Which contract to trade on each trade date (index: trade date, value: contract symbol)."""
    if bars.empty:
        return pd.Series(dtype="object")
    td = trading_date(pd.DatetimeIndex(bars["ts"]))
    daily = bars.assign(trade_date=td).groupby(["trade_date", "symbol"])["volume"].sum().unstack(fill_value=0)
    days = list(daily.index)
    current = daily.iloc[0].idxmax()
    challenger, streak = None, 0
    out = {}
    for day in days:
        out[day] = current  # decided before this session began
        row = daily.loc[day]
        leader = row.idxmax()
        ref = pd.Timestamp(day).date()
        if leader != current and sort_key(leader, ref) > sort_key(current, ref):
            streak = streak + 1 if leader == challenger else 1
            challenger = leader
            if streak >= confirm_days:
                current, challenger, streak = leader, None, 0
        else:
            challenger, streak = None, 0
        # Never stay on a contract that has stopped trading.
        if row.get(current, 0) == 0 and row.sum() > 0:
            later = [s for s in row.index[row > 0] if sort_key(s, ref) >= sort_key(current, ref)]
            if later:
                current = max(later, key=lambda s: row[s])
    return pd.Series(out, name="contract")


def build_continuous(bars: pd.DataFrame, confirm_days: int = 1) -> pd.DataFrame:
    """One continuous 1-minute series following the roll schedule, indexed by UTC bar time."""
    if bars.empty:
        return pd.DataFrame(columns=CONTINUOUS_COLUMNS)
    schedule = roll_schedule(bars, confirm_days)
    b = bars.copy()
    b["trade_date"] = trading_date(pd.DatetimeIndex(b["ts"]))
    b = b[b["symbol"] == b["trade_date"].map(schedule)]
    b = b.sort_values("ts").set_index("ts")
    cont = pd.DataFrame(
        {
            "raw_open": b["open"], "raw_high": b["high"], "raw_low": b["low"], "raw_close": b["close"],
            "volume": b["volume"], "contract": b["symbol"], "trade_date": b["trade_date"],
        }
    )
    cont["roll"] = cont["contract"].ne(cont["contract"].shift()) & cont["contract"].shift().notna()

    # Price gap at each roll: new contract minus old contract, at the last minute both traded before the switch.
    adj = pd.Series(0.0, index=cont.index)
    by_symbol = {s: g.set_index("ts")["close"] for s, g in bars.groupby("symbol")}
    for ts in cont.index[cont["roll"]]:
        new = cont.at[ts, "contract"]
        old = cont["contract"].loc[:ts].iloc[-2]
        before = cont.index[cont.index < ts]
        common = by_symbol[old].index.intersection(by_symbol[new].index)
        common = common[common < ts]
        if len(common):
            t = common[-1]
            gap = by_symbol[new].at[t] - by_symbol[old].at[t]
        else:  # no overlap: compare the last old price with the first new one
            gap = cont.at[ts, "raw_open"] - cont.loc[before[-1], "raw_close"]
        adj[cont.index < ts] += gap
    cont["adjustment"] = adj
    for c in ("open", "high", "low", "close"):
        cont[c] = cont[f"raw_{c}"] + adj
    return cont[CONTINUOUS_COLUMNS]
