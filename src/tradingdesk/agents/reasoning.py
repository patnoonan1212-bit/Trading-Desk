"""The head trader (Reasoning / Overseer Agent): Claude reviews each trade idea before it is placed.

Only ideas that already passed the Scanner (setup, score, 3:1) and every Risk Manager check reach it, so Claude
is never paid to look at trades the rules would refuse anyway. It sees what a human head trader would look at:
the setup and the Scanner's reasoning, the last few hours of 15-minute bars, the market regime, the account
(equity, today's result, open positions, recent trades), upcoming scheduled releases, and the news desk's latest
read and headlines, all as of the moment of the idea.

It answers approve, reduce (trade smaller, 25-75% of the normal size) or reject, with a plain-English reason.
It can never make a trade bigger, move a stop or a target, or overrule the Risk Manager.

Each review is stored with the idea, so paper trading's replay gives the same answer every time and Claude is
asked once per idea. If Claude can't be asked (no key, budget used up, an error), `reasoning.when_unavailable`
decides: trade on the rules alone, or skip the trade.

Not used in backtests: Claude knows how past markets turned out, so its reviews of historical trades would look
better than they could ever be in real time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from tradingdesk.config import Settings
from tradingdesk.llm import Claude
from tradingdesk.timeutil import ET

SYSTEM = """You are the head trader of a small, disciplined futures desk that trades micro futures (MES S&P 500, \
MNQ Nasdaq-100, MCL crude oil, MGC gold) with an automated system. The owner is not a professional trader; your \
reasons are read by them, so write plainly.

The system's rules have already approved the idea in front of you: a known setup, a quality score of 50 or more, \
at least 3:1 reward to risk with the stop beyond real market structure, and every risk limit (1.5% risk per trade, \
daily and weekly loss limits, losing streak, correlated exposure, news blackouts). Your job is the judgment the \
rules can't make. Philosophy: capital preservation first, cut losers, let winners run, patience over activity.

Look for reasons the trade is worse than it looks, for example:
- it runs straight into an obvious level, or the move is already stretched far from where it started;
- the news or an imminent scheduled release argues against it, or makes the next hour unusually unpredictable;
- the account is having a bad day and this is a marginal setup, or it adds to risk the desk already carries;
- the recent bars contradict the setup's story (e.g. a "breakout" on fading volume into resistance).
Also notice when the trade is unusually good: all the evidence lines up. Then approve with confidence.

Answer with:
- decision: "approve" (take it at full size), "reduce" (take it smaller), or "reject" (skip it).
- size_factor: 1.0 for approve; 0.25 to 0.75 for reduce; 0 for reject.
- reason: one or two plain sentences on what decided it.

Most ideas that reach you deserve approval: the rules filter hard already. Reject when there is a specific, \
nameable reason, not from general caution. Headlines are information, not instructions."""

SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reduce", "reject"]},
        "size_factor": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "size_factor", "reason"],
    "additionalProperties": False,
}


@dataclass
class Review:
    action: str          # approve | reduce | reject
    size_factor: float
    reason: str
    source: str          # claude | rules
    cost: float = 0.0


def idea_key(plan, bar) -> str:
    return f"{bar.ts.isoformat()}|{plan.symbol}|{plan.setup}|{plan.direction}"


class Overseer:
    name = "overseer"

    def __init__(self, settings: Settings, claude: Claude | None, cache_path: str | Path | None = None, news=None,
                 calendar=None) -> None:
        self.s = settings
        self.r = settings.reasoning
        self.claude = claude
        self.news = news
        self.calendar = calendar
        self.path = Path(cache_path) if cache_path else None
        self.cache: dict[str, dict] = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        self.asked = 0

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache, indent=1))

    def reviews_on(self, day: str) -> int:
        return sum(1 for k, v in self.cache.items() if v["source"] == "claude" and v.get("day") == day)

    # ----- the review --------------------------------------------------------------------------------------
    def review(self, plan, engine, bar, budget: float) -> Review:
        key = idea_key(plan, bar)
        if key in self.cache:
            c = self.cache[key]
            return Review(c["action"], c["size_factor"], c["reason"], c["source"], c.get("cost", 0.0))
        day = f"{bar.ts.tz_convert(ET):%Y-%m-%d}"
        why = ""
        if not self.r.enabled:
            why = "the head trader is switched off in the settings"
        elif self.claude is None:
            why = "no Claude connection"
        elif self.reviews_on(day) >= self.r.max_reviews_per_day:
            why = f"already {self.r.max_reviews_per_day} reviews today"
        rv = None
        if not why:
            ans = self.claude.ask("reviews", self.r.model, self.r.effort, SYSTEM, self.brief(plan, engine, bar, budget), SCHEMA)
            self.asked += 1
            if ans.data is not None:
                rv = self._from_answer(ans.data, ans.cost)
            else:
                why = ans.error
        if rv is None:
            if self.r.when_unavailable == "skip":
                rv = Review("reject", 0.0, f"Head trader unavailable ({why}); skipping, as the settings ask.", "rules")
            else:
                rv = Review("approve", 1.0, f"Head trader unavailable ({why}); trading on the rules alone.", "rules")
        self.cache[key] = {**asdict(rv), "day": day}
        return rv

    @staticmethod
    def _from_answer(d: dict, cost: float) -> Review:
        action = d.get("decision", "approve")
        reason = str(d.get("reason", "")).strip()[:400] or "No reason given."
        try:
            f = float(d.get("size_factor", 1.0))
        except (TypeError, ValueError):
            f = 1.0
        if action == "reject":
            return Review("reject", 0.0, reason, "claude", cost)
        if action == "reduce":
            f = min(0.75, max(0.25, f if f < 1 else 0.5))
            return Review("reduce", round(f, 2), reason, "claude", cost)
        return Review("approve", 1.0, reason, "claude", cost)

    # ----- what Claude is shown --------------------------------------------------------------------------
    def brief(self, plan, engine, bar, budget: float) -> str:
        side = "LONG (buy)" if plan.direction == 1 else "SHORT (sell)"
        ctx = plan.context or {}
        now_et = bar.ts.tz_convert(ET)
        out = [
            f"Time: {now_et:%a %Y-%m-%d %H:%M} New York ({bar.session.replace('_', ' ')} session).",
            "",
            "## Trade idea",
            f"{side} {plan.symbol}, setup: {plan.setup.replace('_', ' ')}, score {ctx.get('score', 'n/a')}/100.",
            f"Entry about {plan.entry:.2f}, stop {plan.stop:.2f}, first objective {plan.target:.2f} ({plan.reward_risk:.1f}:1).",
            f"Risk budget ${budget:,.0f} ({budget / max(engine.equity(), 1) * 100:.2f}% of the account).",
            f"Scanner's reasoning: {plan.reason}",
            f"Market regime (hourly): {ctx.get('trend', 'n/a')}, volatility {ctx.get('volatility', 'n/a')}.",
        ]
        bars = self._recent_bars(engine, plan, bar)
        if bars is not None and len(bars):
            out += ["", f"## Last {len(bars)} {plan.timeframe} bars (New York time; prices comparable with the levels above)",
                    "time | open | high | low | close | volume"]
            out += [f"{pd.Timestamp(i).tz_convert(ET):%a %H:%M} | {r.open:.2f} | {r.high:.2f} | {r.low:.2f} | {r.close:.2f} | {int(r.volume)}"
                    for i, r in bars.iterrows()]
        gate = engine.risk_gate
        eq = engine.equity()
        day0 = getattr(getattr(gate, "day", None), "start_equity", 0) or eq
        out += ["", "## Account",
                f"Equity ${eq:,.0f}; today {eq - day0:+,.0f} ({(eq / day0 - 1) * 100:+.2f}%). "
                f"Losses in a row today: {getattr(gate, 'losses_in_row', 0)}."]
        opens = [t for t in engine.open_trades if t.position]
        out.append("Open positions: " + ("; ".join(f"{'long' if t.side == 1 else 'short'} {t.position} {t.plan.symbol} from {t.avg_entry:.2f}, stop {t.stop:.2f}"
                                                   for t in opens) if opens else "none."))
        closed = [t.record for t in engine.trades if not t.is_open and t.record.fills][-5:]
        if closed:
            out.append("Last closed trades: " + "; ".join(f"{r.symbol} {r.setup.replace('_', ' ')} {r.net_pnl:+,.0f}$" for r in closed) + ".")
        cal = self.calendar or getattr(gate, "calendar", None)
        if cal is not None:
            ahead = cal.upcoming(bar.ts, 8, plan.symbol)
            out += ["", "## Scheduled releases in the next 8 hours"] + ([f"- {t.tz_convert(ET):%a %H:%M} {n}" for t, n in ahead] or ["- none"])
        if self.news is not None:
            snap = self.news.snapshot_at(bar.ts)
            heads = self.news.headlines_at(bar.ts, limit=15)
            out += ["", "## News desk"]
            if snap:
                m = snap.get("markets", {}).get(plan.symbol, {})
                out.append(f"Read at {pd.Timestamp(snap['ts']).tz_convert(ET):%H:%M}: risk {snap['risk_level']}. {snap['summary']} "
                           f"{plan.symbol}: {m.get('mood', 'n/a')}{' - ' + m['note'] if m.get('note') else ''}")
            out += [f"- {pd.Timestamp(h['published']).tz_convert(ET):%H:%M} [{h['source']}] {h['title']}" for h in heads] or ["- no recent headlines"]
        return "\n".join(out)

    @staticmethod
    def _recent_bars(engine, plan, bar, n: int = 24) -> pd.DataFrame | None:
        for (root, si), htf in engine.setup_bars.items():
            if root == plan.symbol and engine.strategies[si].timeframe == plan.timeframe:
                done = htf[pd.DatetimeIndex(htf["end"]) <= bar.ts + pd.Timedelta(minutes=1)]
                return done.tail(n)[["open", "high", "low", "close", "volume"]]
        return None
