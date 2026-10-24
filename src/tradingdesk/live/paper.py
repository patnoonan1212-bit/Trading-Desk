"""Paper trading on live prices.

Each update ("tick") does this:

  1. Data Agent: fetch the newest 1-minute bars from the free delayed feed, check and clean them, store them.
     News Agent: refresh the economic calendar, read new headlines, and (hourly) ask Claude for the news mood.
  2. Replay: run the full desk (Regime, Scanner, Risk Manager, head trader, Execution with the simulated broker) over every
     bar recorded since paper trading started, plus warm-up history so indicators are ready. Open trades stay
     open at the end.
  3. Compare with the journal of what was already announced. New decisions are appended to the journal (and
     the important ones posted to you); earlier decisions that no longer come out the same are reported.
  4. Watchdog: health checks on data, schedule, positions, risk, history, news feeds and Claude spending.
  5. Write the status page, the trade list and the notification text.

Why replay instead of saving the agents' memory between updates: the recorded prices are the only state, so an
update can run late, run twice or be interrupted without corrupting anything, and code improvements apply
cleanly. It also guarantees paper trading follows exactly the same rules as a backtest on the same prices.
Real-money trading (Phase 6) can't replay real orders; there the broker's positions are the truth and the
Execution Agent reconciles against them.

Files in `paper.state_path`:
    data/        recorded prices (Parquet)
    state.json   when paper trading started, the last update, which decisions differed
    journal.jsonl  every decision as first announced
    status.md    the status page        notify.md  messages for this update (empty if nothing to say)
    trades.csv   closed paper trades    equity.csv equity every 15 minutes
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tradingdesk import readiness
from tradingdesk.agents.news import NewsAgent, http_get
from tradingdesk.agents.reasoning import Overseer
from tradingdesk.agents.risk import RiskManager
from tradingdesk.agents.scanner import ScannerAgent
from tradingdesk.agents.watchdog import Check, Watchdog
from tradingdesk.alerts import EmailAlerter, plain
from tradingdesk.backtest.engine import Backtester
from tradingdesk.backtest.metrics import summarize, trades_frame
from tradingdesk.calendar import EconomicCalendar
from tradingdesk.config import Settings
from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import Decision
from tradingdesk.data.providers.base import HistoricalProvider
from tradingdesk.data.rollover import build_continuous
from tradingdesk.data.store import DataStore
from tradingdesk.data.validate import clean_bars
from tradingdesk.llm import Claude, SpendLedger
from tradingdesk.timeutil import ET

log = logging.getLogger(__name__)

# Routine decisions left off the status page (they're still in the journal): skipped setups and repeated vetoes.
QUIET = {("scanner", "candidate_blocked"), ("scanner", "candidate_dropped"), ("risk", "rejected")}
REFETCH_OVERLAP = pd.Timedelta(hours=2)  # re-ask for the last two hours each time, in case bars arrived late


def decision_key(d: Decision) -> str:
    """What a decision was: when, who, what, on which contract and trade. The wording is left out on purpose:
    after a contract roll, earlier prices are shifted (back-adjusted) so the numbers in old texts change even
    though the decision itself is the same. The journal keeps the original wording, in real contract prices."""
    ts = pd.Timestamp(d.ts).isoformat() if d.ts is not None else ""
    ref = d.details.get("trade_id", d.details.get("setup", ""))
    rule = d.reason.split(":")[0][:40] if (d.agent == "risk" and d.action != "approved") or d.action.startswith("candidate") else ""
    return f"{ts}|{d.agent}|{d.action}|{d.symbol or ''}|{ref}|{rule}"


@dataclass
class TickResult:
    now: pd.Timestamp
    new: list[Decision] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    status_md: str = ""
    notify_md: str = ""
    mirror: object = None
    readiness: list = field(default_factory=list)
    equity: float = 0.0


class PaperTrader:
    def __init__(self, settings: Settings, provider: HistoricalProvider | None = None, claude: Claude | None = None, http=None,
                 alerter: EmailAlerter | None = None, broker_client=None) -> None:
        self.s = settings
        self.dir = Path(settings.paper.state_path)
        self.store = DataStore(self.dir / "data")
        if provider is None:
            if settings.paper.data_provider == "synthetic":
                from tradingdesk.data.providers.synthetic import SyntheticProvider

                provider = SyntheticProvider()
            elif settings.paper.data_provider == "tradovate":
                from tradingdesk.brokers.tradovate import TradovateClient, TradovateMarketData

                provider = TradovateMarketData(TradovateClient(settings.broker.environment))
            else:
                from tradingdesk.data.providers.yahoo import YahooProvider

                provider = YahooProvider()
        self.provider = provider
        self.watchdog = Watchdog(settings)
        self.claude = claude or Claude(settings.reasoning.monthly_budget_usd, SpendLedger(self.dir / "claude_spend.json"))
        self.news = NewsAgent(settings, self.dir, self.claude, http or http_get)
        self.alerter = alerter or EmailAlerter()
        self.broker_client = broker_client

    # ----- state files ---------------------------------------------------------------------------------
    def _state(self) -> dict:
        p = self.dir / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_state(self, st: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "state.json").write_text(json.dumps(st, indent=2, default=str))

    def _journal(self) -> list[dict]:
        p = self.dir / "journal.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []

    # ----- steps ---------------------------------------------------------------------------------------
    def _fetch(self, root: str, start: pd.Timestamp, now: pd.Timestamp) -> tuple[str, bool]:
        """Get new bars for one contract into the store. Returns (error message, used fallback feed)."""
        try:
            raw = self.provider.fetch_bars(root, start, now)
        except Exception as e:  # the Watchdog reports it; other contracts carry on
            log.warning("%s fetch failed: %s", root, e)
            return str(e)[:200], False
        if len(raw):
            clean, _ = clean_bars(raw, root)
            self.store.write_bars(clean, root)
        return "", root in getattr(self.provider, "used_fallback", set())

    def tick(self, now=None) -> TickResult:
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        st = self._state()
        res = TickResult(now=now)
        first = "started_at" not in st
        started = now.floor("min") if first else pd.Timestamp(st["started_at"])
        last_run = None if first else pd.Timestamp(st["last_run"])

        # 1. Data
        data: dict[str, pd.DataFrame] = {}
        for root in self.s.contracts:
            cov = self.store.coverage(root)
            start = now - pd.Timedelta(days=self.s.paper.warmup_days) if cov is None else cov[1] - REFETCH_OVERLAP
            err, fallback = self._fetch(root, start, now)
            raw = self.store.read_bars(root)
            cont = build_continuous(raw, self.s.data.roll.confirm_days) if len(raw) else None
            if cont is not None and len(cont):
                data[root] = cont
            res.checks += self.watchdog.data(root, cont, now, err, fallback)
        res.checks.append(self.watchdog.schedule(last_run, now))
        self.news.update_calendar(now)
        calendar = EconomicCalendar.load(self.s.risk.calendar_file, self.news.calendar_frame())
        self.news.update_headlines(now)
        self.news.update_sentiment(now, calendar)
        res.checks += self.watchdog.news(self.news.status)
        res.checks.append(self.watchdog.claude(self.claude, self.s))

        # 2. Replay
        # The kill switch blocks every trade not yet announced: anything after the newest price seen before it was turned on.
        if self.s.risk.kill_switch and not st.get("kill_switch_since"):
            st["kill_switch_since"] = st.get("last_bar") or now.isoformat()
        elif not self.s.risk.kill_switch:
            st.pop("kill_switch_since", None)
        engine = gate = None
        decisions: list[Decision] = []
        if data:
            reviewer = Overseer(self.s, self.claude, self.dir / "overseer.json", self.news.log, calendar)
            try:
                engine, gate, result = self.replay(data, started, calendar, reviewer, st.get("kill_switch_since"))
            finally:
                reviewer.save()  # keep paid-for reviews even if something later fails
            decisions = [d for d in result.decisions if d.ts is not None and pd.Timestamp(d.ts) >= started]
            res.equity = engine.equity()
            res.checks += self.watchdog.positions(engine)
            res.checks += self.watchdog.risk(gate)
            closed = trades_frame(result.trades)
            self.dir.mkdir(parents=True, exist_ok=True)
            closed.to_csv(self.dir / "trades.csv", index=False)
            result.equity[result.equity.index >= started].rename("equity").to_csv(self.dir / "equity.csv")
        else:
            res.equity = self.s.account.starting_equity
            closed = pd.DataFrame()

        # 3. Broker: copy the twin's positions and stops to Tradovate (or, in a dry run, write down what would be sent).
        res.mirror = None
        st["run_times"] = (st.get("run_times", []) + [now.isoformat()])[-3000:]
        res.readiness = readiness.evaluate(self.s, self.dir, now, self.alerter.configured, st)
        if self.s.broker.name == "tradovate" and engine is not None:
            from tradingdesk.live.mirror import BrokerMirror

            mirror_settings = self.s
            if self.s.mode == "live" and not readiness.ready(res.readiness):
                # Live mode is set, but the checklist hasn't passed: keep everything in dry run and say why.
                missing = [i.name for i in res.readiness if not i.passed]
                res.checks.append(Check("live lock", "alert", "mode is live, but the live-readiness checklist hasn't passed "
                                        f"({', '.join(missing)}). No real orders were sent."))
                mirror_settings = self.s.model_copy(deep=True)
                mirror_settings.broker.dry_run = True
            res.mirror = BrokerMirror(mirror_settings, self.dir, self.broker_client).sync(engine, data, now)
            res.checks.append(self.watchdog.broker(res.mirror, self.s))
            with open(self.dir / "broker_log.jsonl", "a") as f:
                for a in res.mirror.actions + res.mirror.skipped + res.mirror.problems:
                    f.write(json.dumps({"ts": now.isoformat(), "text": a}) + "\n")

        # 4. Journal
        journal = self._journal()
        announced = {j["key"] for j in journal}
        replayed = {decision_key(d) for d in decisions}
        res.new = [d for d in decisions if decision_key(d) not in announced]
        missing = sorted(announced - replayed)
        known = set(st.get("diverged", []))
        newly = [k for k in missing if k not in known]
        res.checks.append(self.watchdog.history(len(missing), len(newly)))
        if res.new:
            with open(self.dir / "journal.jsonl", "a") as f:
                for d in res.new:
                    f.write(json.dumps({"key": decision_key(d), "announced_at": now.isoformat(), **d.to_dict()}, default=str) + "\n")

        # 5. Alerts: by email when it's set up; otherwise (or if sending fails) notify.md goes on the status issue.
        res.notify_md = self._notify(res, engine, closed)
        summary = self._daily_summary(st, now, res, closed, gate)
        outbox = "\n".join(x for x in (res.notify_md, summary) if x)
        sent = False
        if outbox and self.alerter.configured:
            urgent = any(c.status == "alert" for c in res.checks) or "🛑" in res.notify_md
            subject = ("🚨 Trading desk needs attention" if urgent else "Trading desk: end-of-day summary" if summary and not res.notify_md
                       else "Trading desk update")
            sent = self.alerter.send(f"{subject} (paper, equity ${res.equity:,.0f})", plain(outbox) + self._links())
        res.checks.append(self.watchdog.alerts(self.alerter, bool(outbox), sent))
        if any(c.status == "alert" for c in res.checks):
            st["alert_times"] = (st.get("alert_times", []) + [now.isoformat()])[-500:]

        # 6. Pages
        res.status_md = self._status(res, engine, gate, closed, started, calendar)
        (self.dir / "status.md").write_text(res.status_md)
        (self.dir / "notify.md").write_text("" if sent else outbox)
        self._dashboard(res, engine, gate, closed, started, calendar, result.equity if engine else pd.Series(dtype=float))
        if data:
            st["last_bar"] = max(c.index[-1] for c in data.values()).isoformat()
        st.update(started_at=started.isoformat(), last_run=now.isoformat(), diverged=missing, equity=round(res.equity, 2))
        self._save_state(st)
        return res

    def replay(self, data: dict[str, pd.DataFrame], started: pd.Timestamp, calendar: EconomicCalendar, reviewer=None,
               kill_switch_since: str | None = None):
        """Run the desk over the recorded prices. Returns (engine, risk manager, result)."""
        gate = RiskManager(self.s, calendar, news=self.news.log)
        if kill_switch_since:
            gate.kill_switch_from_ns = pd.Timestamp(kill_switch_since).value + 1
        engine = Backtester(self.s, [ScannerAgent(self.s)], data, bus=EventBus(), risk_gate=gate, trade_from=started,
                            close_at_end=False, reviewer=reviewer)
        return engine, gate, engine.run()

    def stored_data(self) -> dict[str, pd.DataFrame]:
        """The recorded prices as continuous series, without fetching anything new."""
        out = {}
        for root in self.s.contracts:
            raw = self.store.read_bars(root)
            if len(raw):
                out[root] = build_continuous(raw, self.s.data.roll.confirm_days)
        return out

    def stored_calendar(self) -> EconomicCalendar:
        return EconomicCalendar.load(self.s.risk.calendar_file, self.news.calendar_frame())

    def started_at(self) -> pd.Timestamp | None:
        st = self._state()
        return pd.Timestamp(st["started_at"]) if "started_at" in st else None

    # ----- alerts ----------------------------------------------------------------------------------------
    @staticmethod
    def _links() -> str:
        import os

        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            return ""
        owner, name = repo.split("/", 1)
        return f"\n\nDashboard: https://{owner}.github.io/{name}/\nStatus page: https://github.com/{repo}/issues?q=label%3Apaper-trading\n"

    def _daily_summary(self, st: dict, now: pd.Timestamp, res: TickResult, closed: pd.DataFrame, gate) -> str:
        """Once per trading day, after the 5 pm New York close: how the day went."""
        et = now.tz_convert(ET)
        day = f"{et:%Y-%m-%d}"
        day_start = st.get("day_start", {})
        if day_start.get("day") != day and et.hour < 17:
            st["day_start"] = {"day": day, "equity": res.equity}
            day_start = st["day_start"]
        if et.weekday() >= 5 or et.hour < 17 or st.get("summary_day") == day:
            return ""
        st["summary_day"] = day
        start_eq = day_start.get("equity", res.equity) if day_start.get("day") == day else res.equity
        pnl = res.equity - start_eq
        lines = [f"**End of day {et:%A %d %B}**", "",
                 f"- Equity ${res.equity:,.0f}, {'up' if pnl >= 0 else 'down'} ${abs(pnl):,.0f} today ({pnl / max(start_eq, 1) * 100:+.2f}%)."]
        today = pd.DataFrame()
        if len(closed):
            ct = pd.to_datetime(closed["closed_at"], utc=True).dt.tz_convert(ET)
            today = closed[(ct.dt.strftime("%Y-%m-%d") == day)]
        if len(today):
            lines.append(f"- {len(today)} trade(s) closed: " + "; ".join(
                f"{r.symbol} {r.direction} {str(r.setup).replace('_', ' ')} {'+' if r.net_pnl >= 0 else '-'}${abs(r.net_pnl):,.0f}"
                for r in today.itertuples()) + ".")
        else:
            lines.append("- No trades closed today.")
        problems = [c for c in res.checks if c.status != "ok"]
        lines.append("- System health: " + ("all normal." if not problems else "; ".join(f"{c.name}: {c.message}" for c in problems[:4])))
        lines.append(f"- Claude spending this month: ${self.claude.ledger.spent():.2f} of ${self.s.reasoning.monthly_budget_usd:.0f}.")
        return "\n".join(lines) + "\n"

    # ----- writing ---------------------------------------------------------------------------------------
    def _dashboard(self, res: TickResult, engine, gate, closed, started, calendar, equity: pd.Series) -> None:
        """Write the dashboard page (site/index.html) for GitHub Pages."""
        from tradingdesk import dashboard

        positions = []
        for t in (engine.open_trades if engine else []):
            if not t.position:
                continue
            last = engine.last_close.get(t.plan.symbol, t.avg_entry)
            positions.append({"symbol": t.plan.symbol, "side": "long" if t.side == 1 else "short", "contracts": t.position,
                              "entry": t.avg_entry, "stop": t.stop, "target": t.plan.target, "setup": t.plan.setup.replace("_", " "),
                              "pnl": (last - t.avg_entry) * t.side * t.position * t.pv})
        reviews, counts = {}, {"total": 0}
        for j in self._journal():
            if j["agent"] == "overseer":
                claude = j.get("details", {}).get("source") == "claude"
                reviews[f"{pd.Timestamp(j['ts']).isoformat()}|{j['symbol']}|{j['details'].get('setup')}"] = \
                    {"approved": "approved", "reduced": "made smaller", "rejected": "passed"}.get(j["action"], j["action"]) \
                    if claude else "not reviewed (rules only)"
                if claude:
                    counts["total"] += 1
                    counts[j["action"]] = counts.get(j["action"], 0) + 1
        learning_p = Path(self.s.paper.learning_file)
        learning = json.loads(learning_p.read_text()) if learning_p.exists() else None
        worst = "alert" if any(c.status == "alert" for c in res.checks) else "warning" if any(c.status == "warning" for c in res.checks) else "ok"
        ledger = self.claude.ledger
        data = dashboard.build_data(
            now=res.now, started=started, equity=res.equity, start_equity=self.s.account.starting_equity,
            day_start_equity=getattr(getattr(gate, "day", None), "start_equity", None) or res.equity,
            open_risk=sum(RiskManager.open_risk(t) for t in engine.open_trades) if engine else 0.0,
            positions=positions, equity_curve=equity[equity.index >= started] if len(equity) else equity,
            closed=closed, reviews=reviews, checks=res.checks, news_snapshot=self.news.log.snapshot_at(res.now),
            upcoming=[{"ts": t.isoformat(), "event": n} for t, n in (calendar.upcoming(res.now, 24) if calendar is not None else [])],
            spend={"spent": ledger.spent(), "reviews": ledger.spent("reviews"), "news": ledger.spent("news"),
                   "budget": self.s.reasoning.monthly_budget_usd, "connected": self.claude.available},
            learning=learning, health=worst, reviews_count=counts,
            readiness=[{"name": i.name, "passed": i.passed, "detail": i.detail} for i in res.readiness])
        site = self.dir / "site"
        site.mkdir(parents=True, exist_ok=True)
        (site / "index.html").write_text(dashboard.render(data))

    def _status(self, res: TickResult, engine, gate, closed: pd.DataFrame, started: pd.Timestamp, calendar=None) -> str:
        eq0 = self.s.account.starting_equity
        worst = "alert" if any(c.status == "alert" for c in res.checks) else "warning" if any(c.status == "warning" for c in res.checks) else "ok"
        health = {"ok": "✅ All systems normal", "warning": "⚠️ Running, with warnings", "alert": "🚨 Needs attention"}[worst]
        md = [
            "# Paper trading desk",
            "",
            f"**{health}** · updated {res.now:%a %Y-%m-%d %H:%M} UTC · paper money only, prices about 10 minutes delayed",
            "",
            f"**Equity:** ${res.equity:,.0f} ({(res.equity / eq0 - 1) * 100:+.2f}% since {started:%Y-%m-%d}, started with ${eq0:,.0f})",
            "",
            "## Open positions",
            "",
        ]
        held = [t for t in (engine.open_trades if engine else []) if t.position or t.state in ("waiting", "starter")]
        if held:
            md += ["| Contract | Side | Contracts | Avg entry | Stop | Target | Open P&L | Setup |", "|---|---|---|---|---|---|---|---|"]
            for t in held:
                last = engine.last_close.get(t.plan.symbol, t.avg_entry)
                pnl = (last - t.avg_entry) * t.side * t.position * t.pv if t.position else 0.0
                md.append(f"| {t.plan.symbol} | {'long' if t.side == 1 else 'short'} | {t.position} | "
                          f"{t.avg_entry:.2f} | {t.stop:.2f} | {t.plan.target:.2f} | ${pnl:,.0f} | {t.plan.setup} ({t.state}) |")
        else:
            md.append("None.")
        md += ["", "## Results so far", ""]
        if len(closed):
            s = summarize(closed)
            md += [f"{s['trades']} closed trades · win rate {s['win_rate']:.0%} · expectancy ${s['expectancy']:,.0f} ({s['expectancy_r']:.2f}R) "
                   f"· net ${s['net_pnl']:,.0f}", "", "| Closed | Contract | Side | Setup | Net P&L | R | Exit |", "|---|---|---|---|---|---|---|"]
            for _, r in closed.sort_values("closed_at", ascending=False).head(10).iterrows():
                md.append(f"| {pd.Timestamp(r.closed_at):%m-%d %H:%M} | {r.symbol} | {r.direction} | {r.setup} | ${r.net_pnl:,.0f} | {r.r:+.2f} | {r.exit} |")
        else:
            md.append("No closed trades yet. The desk is patient: it only trades setups scoring 50+ with 3:1 reward-to-risk.")
        md += ["", "## News and the head trader", ""]
        snap = self.news.log.snapshot_at(res.now)
        if snap:
            moods = ", ".join(f"{k} {v['mood']}" for k, v in snap.get("markets", {}).items())
            md += [f"**News read at {pd.Timestamp(snap['ts']):%a %H:%M} UTC** ({snap['risk_level']} risk): {snap['summary']}", "", f"Mood: {moods}.", ""]
        else:
            md += ["No news read yet.", ""]
        ahead = calendar.upcoming(res.now, 24) if calendar is not None else []
        md.append("**Scheduled releases, next 24h:** " + (", ".join(f"{t.tz_convert(ET):%a %H:%M} ET {n}" for t, n in ahead) or "none") + ".")
        spent, cap = self.claude.ledger.spent(), self.s.reasoning.monthly_budget_usd
        md += ["", f"**Claude spending this month:** ${spent:,.2f} of ${cap:,.0f} "
               f"(trade reviews ${self.claude.ledger.spent('reviews'):,.2f}, news ${self.claude.ledger.spent('news'):,.2f})."]
        if res.readiness:
            md += ["", "## Ready for real money?", "", readiness.markdown(res.readiness)]
        md += ["", "## Watchdog", "", "| | Check | Detail |", "|---|---|---|"]
        md += [f"| {c.icon} | {c.name} | {c.message} |" for c in res.checks]
        recent = [d for d in self._journal() if (d["agent"], d["action"]) not in QUIET][-15:][::-1]
        if recent:
            md += ["", "## Latest decisions", "", "_Every decision, including skipped setups, is in journal.jsonl._", ""]
            md += [f"- {pd.Timestamp(d['ts']):%m-%d %H:%M} · {d['agent']} · {d.get('symbol') or ''} {d['reason']}" for d in recent]
        md += ["", "_Paper trading uses the same rules and fill model as the backtests. Turn trading off with `risk.kill_switch: true` "
               "in config/default.yaml._"]
        return "\n".join(md) + "\n"

    def _notify(self, res: TickResult, engine, closed: pd.DataFrame) -> str:
        mode = self.s.paper.notify
        if mode == "none":
            return ""
        lines = [f"🚨 **Watchdog:** {c.name}: {c.message}" for c in res.checks if c.status == "alert"]
        by_id = {int(r.id): r for _, r in closed.iterrows()} if len(closed) else {}
        for d in sorted(res.new, key=lambda d: pd.Timestamp(d.ts)):
            when = f"{pd.Timestamp(d.ts):%a %H:%M}"
            if d.agent == "risk" and d.action in ("alert", "halt"):
                lines.append(f"🛑 {when} **Risk Manager:** {d.reason}")
            elif d.agent == "risk" and d.action == "shock":
                lines.append(f"⚡ {when} **Sudden move:** {d.reason}")
            elif mode == "trades" and d.agent == "overseer" and d.details.get("source") == "claude":
                verb = {"approved": "approved", "reduced": "made smaller", "rejected": "passed on"}.get(d.action, d.action)
                lines.append(f"🧠 {when} **Head trader {verb} a {d.symbol} trade:** {d.reason}")
            elif mode == "trades" and d.agent == "risk" and d.action == "approved":
                lines.append(f"📥 {when} **New trade, {d.symbol}:** {d.reason}")
            elif mode == "trades" and d.agent == "execution" and d.action == "closed":
                r = by_id.get(int(d.details.get("trade_id", -1)))
                tail = f" Net ${r.net_pnl:,.0f} ({r.r:+.2f}R)." if r is not None else ""
                lines.append(f"📤 {when} **Trade closed, {d.symbol}:** {d.reason}.{tail}")
        m = getattr(res, "mirror", None)
        if m is not None:
            where = f"Tradovate {self.s.broker.environment}" + (" dry run" if self.s.broker.dry_run else "")
            lines += [f"🏦 **{where}:** {'would ' + a[0].lower() + a[1:] if self.s.broker.dry_run else a}" for a in m.actions] if mode == "trades" else []
            lines += [f"🏦 **{where}:** {a}" for a in m.skipped]
            lines += [f"🚨 **{where}:** {a}" for a in m.problems]
        if not lines:
            return ""
        return "\n".join([f"**Paper desk update, {res.now:%a %H:%M} UTC** (equity ${res.equity:,.0f})", ""] + [f"- {x}" for x in lines]) + "\n"
