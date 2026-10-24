"""Risk Manager Agent: the final veto. Every trade idea passes through it, and nothing bypasses it.

Checks, in order (all limits come from the settings file):
  kill switch           no new trades at all while it's on
  circuit breaker       down `max_total_drawdown_pct` (20%) from the account's highest point: stop all new trading
                        until you review it (it never resumes by itself)
  weekly drawdown       down 8% from the start of the week: pause and alert you; resumes next week in
                        backtests (`weekly_resume: auto`) or only when you say so (`manual`)
  daily loss            down 4% from the start of the trading day (open trades included): no more trades today
  losing streak         3 losing trades in a row today: no more trades today
  open positions        at most `max_open_positions` trades at once
  news blackout         no new entries from 10 minutes before to 10 minutes after a high-impact release
  correlated exposure   contracts that move together (MES and MNQ) share one risk budget; a new trade is shrunk
                        to fit what's left, or rejected if nothing useful is left
Sizing itself (1.5% per trade across all entries, and rejecting trades that can't fit even 1 contract) happens
when the trade is built. Every decision is logged with a plain-English reason; limits that should reach you
(weekly pause, daily stop) are also logged as alerts, which Phase 6 sends to your phone.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingdesk.backtest.broker_sim import Bar
from tradingdesk.backtest.engine import Backtester, RiskGate, Verdict
from tradingdesk.backtest.models import TradePlan, TradeRecord
from tradingdesk.calendar import EconomicCalendar
from tradingdesk.config import Settings
from tradingdesk.timeutil import ET


@dataclass
class _Period:
    key: object = None
    start_equity: float = 0.0
    halted: str = ""


class RiskManager(RiskGate):
    name = "risk"

    def __init__(self, settings: Settings, calendar: EconomicCalendar | None = None) -> None:
        self.s = settings
        self.r = settings.risk
        self.calendar = calendar if calendar is not None else EconomicCalendar.load(self.r.calendar_file)
        self.day = _Period()
        self.week = _Period()
        self.losses_in_row = 0
        self.manual_pause = False  # set when the weekly limit hits and weekly_resume is "manual"
        self.counts: dict[str, int] = {}
        self.peak_equity = settings.account.starting_equity
        self.breaker = ""

    # ----- periods ---------------------------------------------------------------------------------------
    @staticmethod
    def _keys(ts: pd.Timestamp) -> tuple[pd.Timestamp, tuple[int, int]]:
        trade_day = (ts.tz_convert(ET) + pd.Timedelta(hours=6)).normalize()
        iso = trade_day.isocalendar()
        return trade_day, (iso.year, iso.week)

    def _roll(self, engine: Backtester, ts: pd.Timestamp) -> None:
        day, week = self._keys(ts)
        equity = engine.equity()
        if day != self.day.key:
            self.day = _Period(day, equity)
            self.losses_in_row = 0
        if week != self.week.key:
            self.week = _Period(week, equity, "" if not self.manual_pause else self.week.halted)

    def _halt(self, period: _Period, reason: str, engine: Backtester, bar: Bar, alert: bool) -> Verdict:
        if not period.halted:
            period.halted = reason
            engine.decide("risk", "alert" if alert else "halt", reason, None, bar.ts)
        return self._no(period.halted)

    def _no(self, reason: str) -> Verdict:
        key = reason.split(":")[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return Verdict(False, reason)

    # ----- the checks ------------------------------------------------------------------------------------
    def check(self, plan: TradePlan, engine: Backtester, bar: Bar) -> Verdict:
        r = self.r
        if r.kill_switch:
            return self._no("Kill switch: the kill switch is on, no new trades.")
        self._roll(engine, bar.ts)
        equity = engine.equity()
        self.peak_equity = max(self.peak_equity, equity)
        if self.breaker:
            return self._no(self.breaker)
        if r.max_total_drawdown_pct > 0:
            dd = (self.peak_equity - equity) / self.peak_equity * 100
            if dd >= r.max_total_drawdown_pct:
                self.breaker = (f"Circuit breaker: the account is down {dd:.1f}% from its peak of ${self.peak_equity:,.0f} "
                                f"(limit {r.max_total_drawdown_pct}%). All new trading is stopped until you review and restart it.")
                engine.decide("risk", "alert", self.breaker, None, bar.ts)
                return self._no(self.breaker)

        if self.week.halted:
            return self._no(self.week.halted)
        week_change = (equity - self.week.start_equity) / self.week.start_equity * 100
        if week_change <= -r.max_weekly_drawdown_pct:
            if r.weekly_resume == "manual":
                self.manual_pause = True
            when = "until you resume it" if r.weekly_resume == "manual" else "until next week"
            return self._halt(self.week, f"Weekly limit: down {abs(week_change):.1f}% this week (limit {r.max_weekly_drawdown_pct}%). Trading paused {when}.", engine, bar, alert=True)

        if self.day.halted:
            return self._no(self.day.halted)
        day_change = (equity - self.day.start_equity) / self.day.start_equity * 100
        if day_change <= -r.max_daily_loss_pct:
            return self._halt(self.day, f"Daily limit: down {abs(day_change):.1f}% today (limit {r.max_daily_loss_pct}%). No more trades today.", engine, bar, alert=True)
        if self.losses_in_row >= r.max_consecutive_losses:
            return self._halt(self.day, f"Losing streak: {self.losses_in_row} losses in a row today. No more trades today.", engine, bar, alert=False)

        if len(engine.open_trades) >= r.max_open_positions:
            return self._no(f"Max positions: already {len(engine.open_trades)} open (limit {r.max_open_positions}).")

        event = self.calendar.blocking_event(bar.ts, r.news_blackout_minutes)
        if event:
            name, at = event
            return self._no(f"News blackout: {name} at {at.tz_convert(ET):%H:%M} ET; no new entries within {r.news_blackout_minutes} minutes of it.")

        per_trade = equity * r.risk_per_trade_pct / 100
        for g in r.correlated_groups:
            if plan.symbol not in g.symbols:
                continue
            used = sum(self.open_risk(t) for t in engine.open_trades if t.plan.symbol in g.symbols)
            room = equity * g.max_combined_risk_pct / 100 - used
            if room < per_trade * 0.25:
                return self._no(f"Correlated exposure: {'/'.join(g.symbols)} already carry ${used:,.0f} of risk (group limit {g.max_combined_risk_pct}% = ${equity * g.max_combined_risk_pct / 100:,.0f}).")
            if room < per_trade:
                return Verdict(True, f"Correlated exposure: {'/'.join(g.symbols)} already carry ${used:,.0f} of risk; this trade is reduced to the ${room:,.0f} left in the group's budget.", room)
        return Verdict(True)

    @staticmethod
    def open_risk(t) -> float:
        """Dollars a live trade could still lose: what it holds to its stop, or its full budget while it's still entering."""
        if t.state in ("waiting", "starter"):
            return t.budget
        return max(0.0, t.position * (t.avg_entry - t.stop) * t.side * t.pv)

    def on_trade_closed(self, record: TradeRecord, engine: Backtester) -> None:
        if not record.fills:
            return
        self.losses_in_row = self.losses_in_row + 1 if record.net_pnl < 0 else 0
