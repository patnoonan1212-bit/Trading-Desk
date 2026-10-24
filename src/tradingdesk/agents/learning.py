"""The Learning Agent: studies every closed trade, finds what works and what doesn't, and suggests setting changes.

What it looks at
  - Results broken down by setup, market regime, volatility, session, hour, direction and Scanner score. Every
    group gets a plain verdict, and a group needs `min_trades` trades before it counts at all: small samples
    are mostly luck.
  - Exits: how often a stopped-out trade went on to reach its target anyway (stops too tight?), and how far
    trades ran in our favour before closing.
  - The head trader: for each idea Claude passed on or made smaller, what the rules alone would have made.
    It replays the paper period without Claude ("rules-only twin") and compares.

Where the trades come from
  - History (if market data has been downloaded): a backtest over all of it. The first two thirds are used to
    find ideas; the last third is kept back to test them.
  - Paper trading: every closed paper trade.

How a suggestion becomes a change
  Each suggestion is a small list of setting changes. It is backtested on the held-back history against the
  current settings, and only passes if it improves the result there (more total R, drawdown no worse). Passed
  suggestions go into a pull request on GitHub showing the exact lines in config/default.yaml and the evidence;
  nothing changes until you merge it. The agent never changes risk limits, and never changes anything by itself.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from tradingdesk.backtest.metrics import equity_stats, trades_frame
from tradingdesk.backtest.walkforward import t_stat
from tradingdesk.config import Settings
from tradingdesk.config_edit import Change, get_path, with_changes
from tradingdesk.timeutil import ET

log = logging.getLogger(__name__)

SCORE_STEPS = [55, 60, 65, 70, 75]
MAX_STOP_BUFFER = 1.0
WARMUP = pd.Timedelta(days=21)


# ----- reading groups of trades ---------------------------------------------------------------------------
def verdict(n: int, t: float, min_trades: int) -> str:
    if n < min_trades:
        return "too early"
    if t >= 2:
        return "clearly positive"
    if t >= 1:
        return "leaning positive"
    if t <= -2:
        return "clearly negative"
    if t <= -1:
        return "leaning negative"
    return "no clear edge"


@dataclass
class Group:
    area: str
    name: str
    trades: int
    win_rate: float
    exp_r: float
    total_r: float
    t: float
    verdict: str


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    opened = pd.to_datetime(df["opened_at"], utc=True, errors="coerce").fillna(pd.to_datetime(df["planned_at"], utc=True))
    df["hour"] = opened.dt.tz_convert(ET).dt.hour.map(lambda h: f"{h:02d}:00 ET")
    df["weekday"] = opened.dt.tz_convert(ET).dt.day_name()
    score = pd.to_numeric(df.get("score"), errors="coerce")
    df["score_band"] = pd.cut(score, [0, 60, 70, 80, 101], right=False, labels=["50-59", "60-69", "70-79", "80+"]).astype(str)
    df["setup_regime"] = df["setup"] + " in " + df["trend"].astype(str)
    df["setup_volatility"] = df["setup"] + " / " + df["volatility"].astype(str) + " volatility"
    return df


AREAS = [("setup", "Setup"), ("trend", "Market regime"), ("volatility", "Volatility"), ("session", "Session"),
         ("hour", "Hour opened"), ("weekday", "Weekday"), ("direction", "Direction"), ("score_band", "Scanner score"),
         ("setup_regime", "Setup by regime"), ("setup_volatility", "Setup by volatility")]


def groups(df: pd.DataFrame, min_trades: int) -> list[Group]:
    out = []
    if df.empty:
        return out
    for col, area in AREAS:
        if col not in df:
            continue
        for name, g in df.groupby(col):
            if str(name) in ("", "nan"):
                continue
            r = g["r"].astype(float)
            out.append(Group(area, str(name), len(g), float((g["net_pnl"] > 0).mean()), float(r.mean()), float(r.sum()),
                             round(t_stat(r), 2), verdict(len(g), t_stat(r), min_trades)))
    return out


def exits(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    stopped = df[df["exit"].astype(str).str.contains("stopped out")]
    known = stopped["stopped_then_target"].dropna()
    return {
        "trades": int(len(df)),
        "stopped_out": int(len(stopped)),
        "stopped_then_target_pct": float(known.astype(bool).mean()) if len(known) else math.nan,
        "reached_1r_pct": float((df["mfe_r"] >= 1).mean()),
        "reached_2r_pct": float((df["mfe_r"] >= 2).mean()),
        "avg_mfe_r": float(df["mfe_r"].mean()),
        "avg_r": float(df["r"].mean()),
    }


# ----- suggestions ----------------------------------------------------------------------------------------
@dataclass
class Proposal:
    id: str
    title: str
    why: str
    changes: list[Change]
    evidence: str
    status: str = "not tested"      # passed | failed | not tested
    test: str = ""                  # what the test on held-back history showed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["changes"] = [c.to_dict() for c in self.changes]
        return d


def _neg(sub: pd.DataFrame, min_trades: int) -> bool:
    return len(sub) >= min_trades and sub["r"].mean() < 0 and t_stat(sub["r"].astype(float)) <= -1


def propose(df: pd.DataFrame, s: Settings, min_trades: int) -> list[Proposal]:
    """Concrete setting changes the evidence supports. Risk limits are never touched."""
    if df.empty:
        return []
    raw = s.model_dump(mode="python")
    out: list[Proposal] = []
    enabled = set(s.scanner.enabled())

    def add(title, why, changes, ev):
        out.append(Proposal(f"P{len(out) + 1}", title, why, changes, ev))

    def ev(sub):
        return f"{len(sub)} trades, average {sub['r'].mean():+.2f}R, total {sub['r'].sum():+.1f}R, t-stat {t_stat(sub['r'].astype(float)):.1f}"

    # 1. A setup that loses overall: switch it off.
    for setup, g in df.groupby("setup"):
        if setup in enabled and _neg(g, min_trades):
            add(f"Switch off {setup.replace('_', ' ')}",
                f"{setup.replace('_', ' ').capitalize()} has lost money across {len(g)} trades, consistently enough that it's unlikely to be bad luck.",
                [Change(f"scanner.setups.{setup}.enabled", True, False)], ev(g))
    off = {c.path.split(".")[2] for p in out for c in p.changes}

    # 2. A setup that loses in one regime (but not overall): stop trading it there.
    for (setup, trend), g in df.groupby(["setup", "trend"]):
        allowed = list(raw["regime"]["allowed"].get(setup, []))
        if setup in off or trend not in allowed or len(allowed) < 2 or not _neg(g, min_trades):
            continue
        rest = df[(df["setup"] == setup) & (df["trend"] != trend)]
        if len(rest) and rest["r"].mean() <= g["r"].mean():
            continue
        add(f"Stop {setup.replace('_', ' ')} when the market is {trend.replace('_', ' ')}",
            f"{setup.replace('_', ' ').capitalize()} loses when the market is {trend.replace('_', ' ')} but does better elsewhere.",
            [Change(f"regime.allowed.{setup}", allowed, [x for x in allowed if x != trend])], ev(g))

    # 3. A setup that loses in high volatility: block it there.
    blocked = list(raw["regime"]["blocked_in_high_volatility"])
    for setup, g in df[df["volatility"] == "high"].groupby("setup"):
        if setup not in off and setup not in blocked and _neg(g, min_trades):
            add(f"Pause {setup.replace('_', ' ')} when volatility is high",
                f"{setup.replace('_', ' ').capitalize()} loses in high-volatility markets.",
                [Change("regime.blocked_in_high_volatility", blocked, blocked + [setup])], ev(g))

    # 4. Low-scoring trades lose: raise the bar.
    score = pd.to_numeric(df["score"], errors="coerce")
    best = None
    for step in SCORE_STEPS:
        if step <= s.scanner.min_score:
            continue
        low, high = df[score < step], df[score >= step]
        if _neg(low, min_trades) and len(high) >= min_trades and high["r"].mean() > 0:
            if best is None or high["r"].sum() > best[2]["r"].sum():
                best = (step, low, high)
    if best:
        step, low, high = best
        add(f"Only take setups scoring {step}+",
            f"Trades the Scanner scored below {step} have lost money; those at {step} and above made money.",
            [Change("scanner.min_score", s.scanner.min_score, int(step))], f"below {step}: {ev(low)}; {step}+: {ev(high)}")

    # 5. Higher scores really do better: size by score (only ever less risk on weaker setups, never more than the cap).
    if not s.execution.score_sizing.enabled:
        weak, strong = df[score < 65], df[score >= 80]
        if len(weak) >= min_trades and len(strong) >= min_trades and strong["r"].mean() - weak["r"].mean() >= 0.2 \
                and strong["r"].mean() > 0:
            add("Risk less on lower-scoring setups",
                f"Setups the Scanner scored 80+ averaged {strong['r'].mean():+.2f}R against {weak['r'].mean():+.2f}R for those under 65, "
                f"so weaker setups get smaller positions (0.75-1.1% risk). The best still risk at most {s.risk.risk_per_trade_pct}%.",
                [Change("execution.score_sizing.enabled", False, True)], f"80+: {ev(strong)}; under 65: {ev(weak)}")

    # 6. Stops too tight: many stopped-out trades went on to hit the target anyway.
    stopped = df[df["exit"].astype(str).str.contains("stopped out")]
    known = stopped["stopped_then_target"].dropna()
    buf = s.execution.stops.atr_buffer
    if len(known) >= min_trades and known.astype(bool).mean() >= 0.4 and buf < MAX_STOP_BUFFER:
        new = round(min(MAX_STOP_BUFFER, buf + 0.25), 2)
        add("Give stops a little more room",
            f"{known.astype(bool).mean():.0%} of stopped-out trades went on to reach their target anyway, a sign stops sit "
            f"where normal noise reaches them. Position sizes shrink to match, so risk per trade stays {s.risk.risk_per_trade_pct}%.",
            [Change("execution.stops.atr_buffer", buf, new)], f"{len(known)} stopped-out trades checked")
    return out


# ----- testing suggestions on held-back history -------------------------------------------------------------
def _backtest(settings: Settings, data: dict, start: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    from tradingdesk.agents.risk import RiskManager
    from tradingdesk.agents.scanner import ScannerAgent
    from tradingdesk.backtest.engine import Backtester

    s = settings.model_copy(deep=True)
    s.mode = "backtest"
    s.risk.max_total_drawdown_pct = 0  # measure the strategy, not the circuit breaker
    window = {k: v[v.index >= start - WARMUP] for k, v in data.items()}
    res = Backtester(s, [ScannerAgent(s)], window, risk_gate=RiskManager(s), trade_from=start).run()
    return trades_frame(res.trades), equity_stats(res.equity, s.account.starting_equity)


def check_proposals(proposals: list[Proposal], settings: Settings, data: dict, start: pd.Timestamp) -> dict:
    """Backtest each suggestion against the current settings on history from `start` on (not used to find them)."""
    base_trades, base_eq = _backtest(settings, data, start)
    base_r = float(base_trades["r"].sum()) if len(base_trades) else 0.0
    base_dd = abs(base_eq.get("max_drawdown_pct", 0.0))
    for p in proposals:
        try:
            trades, eq = _backtest(with_changes(settings, p.changes), data, start)
        except Exception as e:  # a suggestion that can't even be tested is not applied
            p.status, p.test = "failed", f"could not be tested: {e}"
            continue
        r = float(trades["r"].sum()) if len(trades) else 0.0
        dd = abs(eq.get("max_drawdown_pct", 0.0))
        better = r - base_r >= max(2.0, 0.05 * abs(base_r)) and dd <= base_dd + 1.5
        p.status = "passed" if better else "failed"
        p.test = (f"On held-back history: {len(trades)} trades, total {r:+.1f}R, worst drop {dd:.1f}% "
                  f"vs current settings {len(base_trades)} trades, {base_r:+.1f}R, worst drop {base_dd:.1f}%.")
    return {"trades": int(len(base_trades)), "total_r": base_r, "max_drawdown_pct": base_dd}


# ----- the head trader's scorecard ---------------------------------------------------------------------------
def head_trader_scorecard(paper_dir: Path, settings: Settings) -> dict:
    """Compare what Claude decided with what the rules alone would have done over the paper period."""
    from tradingdesk.live.paper import PaperTrader

    journal_p = paper_dir / "journal.jsonl"
    if not journal_p.exists():
        return {}
    journal = [json.loads(x) for x in journal_p.read_text().splitlines() if x.strip()]
    reviews = [j for j in journal if j["agent"] == "overseer" and j.get("details", {}).get("source") == "claude"]
    if not reviews:
        return {"reviewed": 0}
    s = settings.model_copy(deep=True)
    s.paper.state_path = paper_dir
    pt = PaperTrader(s, provider=object(), http=lambda url: b"")
    data, started = pt.stored_data(), pt.started_at()
    if not data or started is None:
        return {"reviewed": len(reviews)}
    st = json.loads((paper_dir / "state.json").read_text()) if (paper_dir / "state.json").exists() else {}
    engine, _, twin = pt.replay(data, started, pt.stored_calendar(), None, st.get("kill_switch_since"))
    twin_df = trades_frame(twin.trades)
    actual_eq = st.get("equity", s.account.starting_equity)

    def outcome(rv):
        if twin_df.empty:
            return None
        ts = pd.Timestamp(rv["ts"])
        m = twin_df[(pd.to_datetime(twin_df["planned_at"], utc=True) == ts) & (twin_df["symbol"] == rv["symbol"])
                    & (twin_df["setup"] == rv["details"].get("setup"))]
        return None if m.empty else m.iloc[0]

    rows = []
    for rv in reviews:
        o = outcome(rv)
        rows.append({"action": rv["action"], "symbol": rv["symbol"], "ts": rv["ts"], "reason": rv["reason"],
                     "rules_r": None if o is None else float(o["r"]), "rules_pnl": None if o is None else float(o["net_pnl"]),
                     "factor": rv["details"].get("factor")})
    rej = [r for r in rows if r["action"] == "rejected" and r["rules_r"] is not None]
    red = [r for r in rows if r["action"] == "reduced" and r["rules_r"] is not None]
    saved = -sum(r["rules_pnl"] for r in rej) - sum(r["rules_pnl"] * (1 - (r["factor"] or 0.5)) for r in red)
    return {
        "reviewed": len(rows),
        "approved": sum(r["action"] == "approved" for r in rows),
        "reduced": sum(r["action"] == "reduced" for r in rows),
        "rejected": sum(r["action"] == "rejected" for r in rows),
        "rejected_rules_r": round(sum(r["rules_r"] for r in rej), 2),
        "rejected_rules_wins": sum(r["rules_r"] > 0 for r in rej),
        "rejected_known": len(rej),
        "dollars_saved": round(saved, 2),
        "paper_equity": actual_eq,
        "rules_only_equity": round(engine.equity(), 2),
        "decisions": rows[-30:],
    }


# ----- the whole review --------------------------------------------------------------------------------------
@dataclass
class LearningReport:
    generated: pd.Timestamp
    sources: list[str]
    trades: pd.DataFrame
    groups: list[Group]
    exits: dict
    proposals: list[Proposal]
    baseline: dict = field(default_factory=dict)
    head_trader: dict = field(default_factory=dict)
    test_from: pd.Timestamp | None = None
    min_trades: int = 30

    def to_json(self) -> dict:
        strong = [asdict(g) for g in self.groups if g.verdict != "too early"]
        return {
            "generated": self.generated.isoformat(), "sources": self.sources, "trades": int(len(self.trades)),
            "min_trades": self.min_trades, "groups": strong, "exits": _clean(self.exits),
            "proposals": [p.to_dict() for p in self.proposals], "baseline": self.baseline,
            "head_trader": self.head_trader, "test_from": self.test_from.isoformat() if self.test_from is not None else None,
        }


def _clean(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in d.items()}


class LearningAgent:
    name = "learning"

    def __init__(self, settings: Settings, min_trades: int = 30) -> None:
        self.s = settings
        self.min_trades = min_trades

    def review(self, history: dict[str, pd.DataFrame] | None = None, paper_dir: str | Path | None = None,
               now: pd.Timestamp | None = None) -> LearningReport:
        now = now or pd.Timestamp.now(tz="UTC")
        parts, sources, test_from, baseline = [], [], None, {}
        if history:
            start = min(v.index[0] for v in history.values()) + WARMUP
            end = max(v.index[-1] for v in history.values())
            test_from = start + (end - start) * 2 / 3
            trades, _ = _backtest(self.s, {k: v[v.index < test_from] for k, v in history.items()}, start)
            parts.append(trades.assign(source="history"))
            sources.append(f"backtest on history {start:%Y-%m-%d} to {test_from:%Y-%m-%d} (the rest, to {end:%Y-%m-%d}, is kept back for testing)")
        paper_dir = Path(paper_dir) if paper_dir else None
        if paper_dir and (paper_dir / "trades.csv").exists() and (paper_dir / "trades.csv").stat().st_size > 2:
            pt = pd.read_csv(paper_dir / "trades.csv")
            if len(pt):
                parts.append(pt.assign(source="paper"))
                sources.append(f"{len(pt)} closed paper trades")
        df = enrich(pd.concat([p for p in parts if len(p)], ignore_index=True)) if any(len(p) for p in parts) else pd.DataFrame()
        props = propose(df, self.s, self.min_trades)
        if props and history and test_from is not None:
            baseline = check_proposals(props, self.s, history, test_from)
        for p in props:
            if p.status == "not tested":
                p.test = "Not tested yet: needs downloaded market history to test on."
        ht = head_trader_scorecard(paper_dir, self.s) if paper_dir else {}
        return LearningReport(now, sources, df, groups(df, self.min_trades), exits(df), props, baseline, ht, test_from, self.min_trades)


# ----- writing it up -----------------------------------------------------------------------------------------
def report_markdown(rep: LearningReport) -> str:
    md = ["# Learning review", "", f"_{rep.generated:%A %Y-%m-%d %H:%M} UTC_", ""]
    if rep.trades.empty:
        md += ["Nothing to learn from yet: no closed trades in paper trading and no downloaded history.", ""]
    else:
        md += [f"**Based on {len(rep.trades)} trades:** " + "; ".join(rep.sources) + ".",
               f"A group needs {rep.min_trades}+ trades before it counts; fewer is mostly luck.", ""]
    passed = [p for p in rep.proposals if p.status == "passed"]
    md += ["## Suggested changes", ""]
    if not rep.proposals:
        md.append("None this week. Nothing in the results is clear enough to act on." if not rep.trades.empty else "None yet.")
        md.append("")
    for p in rep.proposals:
        icon = {"passed": "✅", "failed": "❌", "not tested": "⏳"}[p.status]
        md += [f"### {icon} {p.id}: {p.title}", "", p.why, "", f"- Evidence: {p.evidence}", f"- Test: {p.test}"]
        md += [f"- Change: `{c.path}` from `{c.old}` to `{c.new}`" for c in p.changes] + [""]
    if passed:
        md += [f"**{len(passed)} suggestion(s) passed the test** and are in a pull request for you to approve. "
               "Merging it changes the settings; closing it leaves everything as it is.", ""]
    strong = sorted([g for g in rep.groups if g.verdict in ("clearly positive", "clearly negative", "leaning positive", "leaning negative")],
                    key=lambda g: -abs(g.t))
    md += ["## What stands out", "",
           "_With this many groups, a few will look clear just by chance. That's why only suggestions that also pass a "
           "test on held-back history are ever offered._", ""]
    if strong:
        md += ["| Area | Group | Trades | Win rate | Avg R | Total R | Verdict |", "|---|---|---|---|---|---|---|"]
        md += [f"| {g.area} | {g.name.replace('_', ' ')} | {g.trades} | {g.win_rate:.0%} | {g.exp_r:+.2f} | {g.total_r:+.1f} | {g.verdict} |"
               for g in strong[:20]]
    else:
        md.append("Nothing yet: no group has enough trades with a clear enough result.")
    e = rep.exits
    if e:
        stt = e.get("stopped_then_target_pct")
        after = f"; {stt:.0%} of those reached the target afterwards." if stt is not None and not math.isnan(stt) else "."
        md += ["", "## Exits", "",
               f"- {e['reached_1r_pct']:.0%} of trades got at least 1R in our favour at some point, {e['reached_2r_pct']:.0%} reached 2R.",
               f"- {e['stopped_out']} were stopped out{after}"]
    h = rep.head_trader
    md += ["", "## The head trader (Claude)", ""]
    if not h or not h.get("reviewed"):
        md.append("No trades reviewed by Claude yet.")
    else:
        md.append(f"Claude reviewed {h['reviewed']} trade ideas: approved {h['approved']}, made {h['reduced']} smaller, passed on {h['rejected']}.")
        if h.get("rejected_known"):
            md.append(f"The {h['rejected_known']} it passed on would have made {h['rejected_rules_r']:+.1f}R under the rules "
                      f"({h['rejected_rules_wins']} winners).")
        if "rules_only_equity" in h:
            diff = h["paper_equity"] - h["rules_only_equity"]
            md.append(f"Paper account ${h['paper_equity']:,.0f} vs ${h['rules_only_equity']:,.0f} with the rules alone: "
                      f"Claude has {'added' if diff >= 0 else 'cost'} ${abs(diff):,.0f} so far, before its own fees. "
                      + ("Too few reviews to judge yet." if h["reviewed"] < 30 else ""))
    return "\n".join(md) + "\n"


def write(rep: LearningReport, out: str | Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report_markdown(rep))
    (out / "latest.json").write_text(json.dumps(rep.to_json(), indent=1, default=_json_default))


def _json_default(o):
    if isinstance(o, np.integer | np.floating):
        return o.item()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


def load_proposals(path: str | Path) -> list[Proposal]:
    d = json.loads(Path(path).read_text())
    return [Proposal(p["id"], p["title"], p["why"], [Change(**c) for c in p["changes"]], p["evidence"], p["status"], p["test"])
            for p in d.get("proposals", [])]


def changes_unchanged(s: Settings, p: Proposal) -> bool:
    """Is each setting still what it was when the suggestion was made?"""
    raw = s.model_dump(mode="python")
    try:
        return all(get_path(raw, c.path) == c.old for c in p.changes)
    except KeyError:
        return False
