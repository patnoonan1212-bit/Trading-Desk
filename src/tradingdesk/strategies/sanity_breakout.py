"""ENGINE CHECK ONLY. A plain breakout strategy used to exercise the backtester; it is not meant to trade.

Long when a 15-minute bar closes above the highest high of the previous 20 bars while the 50-bar EMA is
rising (short is the mirror image). Stop goes below the lowest low of the last 10 bars plus an ATR buffer;
the first objective is 3R. A 1-minute close back above the breakout level confirms the trade.
Real strategies arrive with the Scanner in Phase 2.
"""

from __future__ import annotations

import pandas as pd

from tradingdesk.backtest.models import TradePlan
from tradingdesk.backtest.strategy import Context, Strategy
from tradingdesk.indicators import atr, ema, prior_high, prior_low


class SanityBreakout(Strategy):
    name = "sanity_breakout"

    def __init__(self, timeframe: str = "15m", lookback: int = 20, stop_lookback: int = 10, atr_buffer: float = 0.25, reward_risk: float = 3.0) -> None:
        self.timeframe = timeframe
        self.lookback = lookback
        self.stop_lookback = stop_lookback
        self.atr_buffer = atr_buffer
        self.reward_risk = reward_risk

    def prepare(self, symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
        b = bars.copy()
        b["atr"] = atr(b)
        b["ema"] = ema(b["close"], 50)
        b["ema_slope"] = b["ema"].diff(5)
        b["hh"] = prior_high(b, self.lookback)
        b["ll"] = prior_low(b, self.lookback)
        b["swing_low"] = b["low"].rolling(self.stop_lookback).min()
        b["swing_high"] = b["high"].rolling(self.stop_lookback).max()
        return b

    def on_bar(self, symbol: str, bars: pd.DataFrame, i: int, ctx: Context) -> list[TradePlan]:
        if ctx.has_trade(symbol):
            return []
        r = bars.iloc[i]
        if pd.isna(r["hh"]) or pd.isna(r["atr"]) or pd.isna(r["ema_slope"]) or r["atr"] <= 0:
            return []
        if r["close"] > r["hh"] and r["ema_slope"] > 0:
            stop = r["swing_low"] - self.atr_buffer * r["atr"]
            risk = r["close"] - stop
            return [TradePlan(symbol, 1, r["close"], stop, r["close"] + self.reward_risk * risk, self.name,
                              f"15m close {r['close']:.2f} broke above the 20-bar high {r['hh']:.2f} with a rising 50 EMA.",
                              self.timeframe, confirm_level=r["hh"])]
        if r["close"] < r["ll"] and r["ema_slope"] < 0:
            stop = r["swing_high"] + self.atr_buffer * r["atr"]
            risk = stop - r["close"]
            return [TradePlan(symbol, -1, r["close"], stop, r["close"] - self.reward_risk * risk, self.name,
                              f"15m close {r['close']:.2f} broke below the 20-bar low {r['ll']:.2f} with a falling 50 EMA.",
                              self.timeframe, confirm_level=r["ll"])]
        return []
