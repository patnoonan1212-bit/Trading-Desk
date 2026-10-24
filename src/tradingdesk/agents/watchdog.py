"""The Watchdog: checks after every paper-trading update that the system is healthy, and says so in plain English.

    data feed     is the newest price recent enough, given that the market is open and the free feed runs ~10 min behind?
    data gaps     holes in the recent prices while the market was open
    feed source   did we have to fall back to a less reliable price series?
    schedule      are updates arriving regularly, or were runs missed while the market was open?
    positions     does every open position have a working stop for its full size?
    risk          is any trade carrying more risk than it was given? is the Risk Manager halting trading?
    history       does replaying the recorded prices still give the same decisions that were announced?
    news          are the live calendar and the news feeds answering?
    claude        is the head trader connected, and how much of the month's Claude budget is used?
    alerts        are email alerts set up, and did the last one go out?
    broker        does Tradovate match the desk, and is every position protected by a real stop?

Each check comes back as ok, warning or alert. Alerts are posted to you (see paper.notify in the settings).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingdesk.config import Settings
from tradingdesk.data.validate import find_gaps, trading_schedule

FEED_DELAY_MINUTES = 10  # how far behind the free feed normally runs


@dataclass
class Check:
    name: str
    status: str   # ok | warning | alert
    message: str

    @property
    def icon(self) -> str:
        return {"ok": "✅", "warning": "⚠️", "alert": "🚨"}[self.status]


def open_minutes(root: str, a: pd.Timestamp, b: pd.Timestamp) -> float:
    """Minutes between a and b during which the exchange was scheduled to be open."""
    if b <= a:
        return 0.0
    sched = trading_schedule(root, a - pd.Timedelta(days=1), b)
    lo = np.maximum(pd.to_datetime(sched["open"], utc=True), a)
    hi = np.minimum(pd.to_datetime(sched["close"], utc=True), b)
    return float(((hi - lo).dt.total_seconds() / 60).clip(lower=0).sum())


class Watchdog:
    name = "watchdog"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.w = settings.watchdog

    def data(self, root: str, cont: pd.DataFrame | None, now: pd.Timestamp, error: str = "", fallback: bool = False) -> list[Check]:
        out = []
        if error:
            out.append(Check(f"{root} data feed", "alert", f"Could not get prices: {error}"))
        if cont is None or cont.empty:
            if not error:
                out.append(Check(f"{root} data feed", "alert", "No prices stored yet."))
            return out
        last = cont.index[-1]
        missing = open_minutes(root, last + pd.Timedelta(minutes=1), now - pd.Timedelta(minutes=FEED_DELAY_MINUTES))
        if missing > self.w.max_data_delay_minutes:
            out.append(Check(f"{root} data feed", "alert",
                             f"Newest price is from {last:%a %H:%M} UTC, {missing:.0f} market-open minutes later than expected. No new trades can be judged on {root}."))
        elif not error:
            out.append(Check(f"{root} data feed", "ok", f"Newest price {last:%a %H:%M} UTC ({(now - last).total_seconds() / 60:.0f} min ago)."))
        recent = cont[cont.index >= now - pd.Timedelta(hours=self.w.gap_check_hours)]
        gaps = find_gaps(recent, root)
        if len(gaps):
            out.append(Check(f"{root} data gaps", "warning",
                             f"{len(gaps)} hole(s) in the last {self.w.gap_check_hours}h while the market was open (longest {int(gaps['minutes'].max())} min)."))
        if fallback:
            out.append(Check(f"{root} feed source", "warning",
                             f"Using Yahoo's rolling {root}=F series because the contract months weren't available; contract rolls may show as price jumps."))
        return out

    def schedule(self, last_run: pd.Timestamp | None, now: pd.Timestamp) -> Check:
        if last_run is None:
            return Check("update schedule", "ok", "First update.")
        gap = open_minutes(self.s.contracts[0], last_run, now)
        if gap > self.w.max_minutes_between_runs:
            return Check("update schedule", "warning",
                         f"The previous update was at {last_run:%a %H:%M} UTC, {gap:.0f} market-open minutes ago. Runs were missed or delayed; "
                         "no trades were lost (prices are replayed), but alerts arrived late.")
        return Check("update schedule", "ok", f"Previous update {last_run:%a %H:%M} UTC.")

    def positions(self, engine) -> list[Check]:
        out = []
        for t in engine.open_trades:
            if not t.position:
                continue
            stop = t.working("stop")
            if stop is None or stop.remaining < t.position:
                out.append(Check(f"{t.plan.symbol} protection", "alert",
                                 f"Trade {t.record.id} holds {t.position} contract(s) without a full working stop."))
            used = t.risk_used("overnight")
            if used > t.budget * 1.25 and not t.breakeven_done:
                out.append(Check(f"{t.plan.symbol} risk", "warning",
                                 f"Trade {t.record.id} could lose ${used:,.0f}, more than its ${t.budget:,.0f} budget (likely extra slippage or a gap)."))
        if not out:
            n = sum(1 for t in engine.open_trades if t.position)
            out.append(Check("positions", "ok", f"{n} open position(s), all protected by stops." if n else "No open positions."))
        return out

    def risk(self, gate) -> list[Check]:
        if gate is None:
            return []
        if self.s.risk.kill_switch:
            return [Check("risk manager", "warning", "Kill switch is ON: no new trades.")]
        if getattr(gate, "breaker", ""):
            return [Check("risk manager", "alert", gate.breaker)]
        halted = getattr(gate, "week", None) and gate.week.halted or getattr(gate, "day", None) and gate.day.halted
        if halted:
            return [Check("risk manager", "warning", halted)]
        return [Check("risk manager", "ok", "Trading allowed; no limits hit.")]

    def history(self, missing: int, newly_missing: int) -> Check:
        if newly_missing:
            return Check("history", "alert",
                         f"{newly_missing} earlier decision(s) no longer come out the same when the recorded prices are replayed. "
                         "This happens after a settings or code change, or if the data provider revised old prices. "
                         "The journal keeps what was originally announced; the account figures now follow the current rules.")
        if missing:
            return Check("history", "warning", f"{missing} earlier decision(s) differ from the replay (already reported).")
        return Check("history", "ok", "Replaying the recorded prices reproduces every announced decision.")

    def news(self, status: dict[str, str]) -> list[Check]:
        out = []
        cal = status.get("calendar", "")
        if cal.startswith("could not"):
            out.append(Check("news calendar", "warning", f"{cal}; the built-in list of jobs, inflation and Fed dates still applies."))
        heads = status.get("headlines", "")
        if heads.startswith("0 of") and self.s.news.feeds:
            out.append(Check("news feeds", "warning", "No news feed answered; headlines and the news mood are out of date."))
        if not out:
            parts = [x for x in (cal, heads, status.get("sentiment", "")) if x]
            out.append(Check("news", "ok", ("; ".join(parts) + ".") if parts else "Up to date."))
        return out

    def claude(self, claude, settings: Settings) -> Check:
        r = settings.reasoning
        if not r.enabled:
            return Check("head trader", "ok", "Switched off in the settings; trades follow the rules alone.")
        if not claude.available:
            return Check("head trader", "warning", "Not connected: add the ANTHROPIC_API_KEY secret to turn on Claude's trade reviews and "
                                                   "news reading. Until then trades follow the rules alone.")
        spent, cap = claude.ledger.spent(), r.monthly_budget_usd
        if spent >= cap:
            return Check("head trader", "warning", f"This month's ${cap:.0f} Claude budget is used up; trades follow the rules alone until next month.")
        if spent >= 0.8 * cap:
            return Check("head trader", "warning", f"${spent:.2f} of the ${cap:.0f} monthly Claude budget used.")
        return Check("head trader", "ok", f"Connected; ${spent:.2f} of the ${cap:.0f} monthly budget used.")

    def alerts(self, alerter, had_news: bool, sent: bool) -> Check:
        if not alerter.configured:
            return Check("email alerts", "ok", "Not set up; news is posted on the status issue instead (GitHub emails you about it).")
        if had_news and not sent:
            return Check("email alerts", "alert", f"An alert email could not be sent ({alerter.last_error}); it was posted on the status issue instead.")
        return Check("email alerts", "ok", f"Set up; alerts go to {alerter.to}." + (" Sent one this update." if sent else ""))

    def broker(self, result, settings: Settings) -> Check:
        b = settings.broker
        where = f"Tradovate {b.environment}" + (" (dry run)" if b.dry_run else "")
        if result.problems:
            return Check("broker", "alert", f"{where}: " + " ".join(result.problems))
        if result.halted:
            return Check("broker", "warning", f"{where}: {result.halted}")
        did = f"{len(result.actions)} order change(s) this update" if result.actions else "already matched the desk"
        return Check("broker", "ok", f"{where}: {did}.")
