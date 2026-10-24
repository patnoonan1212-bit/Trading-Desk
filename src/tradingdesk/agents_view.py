"""What the dashboard's Agents view shows: each agent's status and the messages passed between them.

Agents never call each other directly; they publish decisions on the event bus, and every decision is kept in the
journal. Here those decisions are turned back into who-told-whom: a Scanner idea is a message from the Scanner to
the Risk Manager, an approval goes on to the head trader, the head trader's go-ahead to Execution, and so on.
Things that happen every update without a journal entry (new prices, the news read) are added as messages too.
"""

from __future__ import annotations

import pandas as pd

from tradingdesk.timeutil import ET

# Agents, the role group they sit in, and a one-line job description.
AGENTS = {
    "data": ("Data", "inputs", "Fetches and checks prices"),
    "news": ("News & Sentiment", "inputs", "Calendar, headlines, mood"),
    "regime": ("Market Regime", "analysis", "Trend and volatility"),
    "scanner": ("Scanner", "analysis", "Finds and scores setups"),
    "risk": ("Risk Manager", "decision", "Final veto, limits, sizing"),
    "overseer": ("Head trader", "decision", "Claude reviews each idea"),
    "execution": ("Execution", "action", "Places and manages orders"),
    "broker": ("Broker", "action", "Where orders are filled"),
    "watchdog": ("Watchdog", "oversight", "Checks system health"),
    "learning": ("Learning", "oversight", "Weekly review of results"),
    "you": ("You", "oversight", "Alerts and approvals"),
}

# The lines in the diagram: (from, to, what travels along it).
EDGES = [
    ("data", "regime", "prices"), ("data", "scanner", "prices"), ("news", "risk", "calendar"),
    ("news", "overseer", "headlines, mood"), ("regime", "scanner", "regime"), ("scanner", "risk", "trade ideas"),
    ("risk", "overseer", "approved ideas"), ("overseer", "execution", "go-ahead"), ("risk", "execution", "size"),
    ("execution", "broker", "orders"), ("learning", "overseer", "findings"), ("watchdog", "you", "alerts"),
    ("execution", "you", "trade news"), ("risk", "you", "limit alerts"), ("overseer", "scanner", "passes"),
]

QUIET = {"candidate_dropped"}  # too many to show; still in the journal


def _msg(ts, frm, to, kind, text, fresh) -> dict:
    return {"ts": pd.Timestamp(ts).isoformat(), "from": frm, "to": to, "kind": kind, "text": text, "fresh": fresh}


def messages_from_journal(journal: list[dict], now: pd.Timestamp, limit: int = 80) -> list[dict]:
    """Journal decisions as messages between agents. kind: idea | approve | veto | order | alert | info."""
    out = []
    now_iso = now.isoformat()
    for j in journal:
        agent, action, reason, sym = j.get("agent"), j.get("action"), j.get("reason", ""), j.get("symbol") or ""
        fresh = j.get("announced_at") == now_iso
        ts = j.get("ts") or j.get("announced_at")
        details = j.get("details") or {}
        text = f"{sym}: {reason}" if sym and not reason.startswith(sym) else reason
        if action in QUIET:
            continue
        if agent == "scanner" and action == "trade_idea":
            out.append(_msg(ts, "scanner", "risk", "idea", text, fresh))
        elif agent == "scanner" and action == "candidate_blocked":
            out.append(_msg(ts, "regime", "scanner", "info", text, fresh))
        elif agent == "risk" and action == "approved":
            out.append(_msg(ts, "risk", "overseer", "approve", text, fresh))
        elif agent == "risk" and action == "rejected":
            out.append(_msg(ts, "risk", "scanner", "veto", text, fresh))
        elif agent == "risk" and action in ("reduced", "brake", "sized"):
            out.append(_msg(ts, "risk", "execution", "info", text, fresh))
        elif agent == "risk" and action in ("alert", "halt"):
            out.append(_msg(ts, "risk", "you", "alert", text, fresh))
        elif agent == "risk" and action == "shock":
            out.append(_msg(ts, "data", "risk", "alert", text, fresh))
        elif agent == "overseer":
            who = "" if details.get("source") == "claude" else " (rules only)"
            if action == "rejected":
                out.append(_msg(ts, "overseer", "scanner", "veto", f"Passed{who}: {text}", fresh))
            else:
                verb = "Smaller" if action == "reduced" else "Go"
                out.append(_msg(ts, "overseer", "execution", "approve", f"{verb}{who}: {text}", fresh))
        elif agent == "execution" and action == "closed":
            out.append(_msg(ts, "execution", "you", "order", f"Trade closed: {text}", fresh))
        elif agent == "execution":
            out.append(_msg(ts, "execution", "broker", "order", text, fresh))
    return out[-limit:]


def _ago(ts, now) -> str:
    if ts is None:
        return "not yet"
    mins = (now - pd.Timestamp(ts)).total_seconds() / 60
    if mins < 1:
        return "just now"
    if mins < 90:
        return f"{mins:.0f} min ago"
    if mins < 48 * 60:
        return f"{mins / 60:.0f} h ago"
    return f"{mins / 1440:.0f} days ago"


def build(*, now, journal, new_count_by_agent, checks, data, engine, news_status, news_snapshot, claude_ok, reasoning_on,
          learning, broker, alerts_by_email, market_open, prices_new) -> dict:
    """Status of each agent for this update, and the recent conversation."""
    msgs = messages_from_journal(journal, now)
    # Things that happen every update without a journal entry.
    if prices_new and data:
        newest = max(v.index[-1] for v in data.values())
        line = f"Fresh 1-minute prices for {', '.join(data)} (newest {newest.tz_convert(ET):%H:%M} ET)."
        msgs += [_msg(now, "data", "regime", "info", line, True), _msg(now, "data", "scanner", "info", line, True)]
    if news_snapshot and pd.Timestamp(news_snapshot["ts"]) == now:
        msgs.append(_msg(now, "news", "overseer", "info", f"News read: {news_snapshot.get('summary', '')}", True))
    alerts = [c for c in checks if c.status == "alert"]
    for c in alerts:
        msgs.append(_msg(now, "watchdog", "you", "alert", f"{c.name}: {c.message}", True))
    msgs.sort(key=lambda m: m["ts"])

    # An agent is "working" if it sent or received a message in this update.
    last_by_agent: dict[str, str] = {}
    for m in msgs:
        last_by_agent[m["from"]] = max(m["ts"], last_by_agent.get(m["from"], ""))
        last_by_agent[m["to"]] = max(m["ts"], last_by_agent.get(m["to"], ""))
    fresh_from = {m["from"] for m in msgs if m["fresh"]} | {m["to"] for m in msgs if m["fresh"]}

    def status(key, problem=False, off=False):
        if off:
            return "off"
        if problem:
            return "problem"
        return "working" if key in fresh_from else "idle"

    data_problem = any(c.status == "alert" and "data" in c.name for c in checks)
    regimes, regime_list = [], []
    if engine is not None:
        for (root, _si), htf in engine.setup_bars.items():
            done = htf[pd.DatetimeIndex(htf["end"]) <= now]
            if len(done) and "trend" in done:
                tr, vol = str(done["trend"].iloc[-1]), str(done.get("volatility", pd.Series(["normal"])).iloc[-1])
                regimes.append(f"{root} {tr.replace('_', ' ')}")
                regime_list.append({"symbol": root, "trend": tr, "volatility": vol})
    ideas_today = [m for m in msgs if m["from"] == "scanner" and m["kind"] == "idea"
                   and pd.Timestamp(m["ts"]).tz_convert(ET).date() == now.tz_convert(ET).date()]
    open_pos = [t for t in (engine.open_trades if engine else []) if t.position]
    gate = engine.risk_gate if engine is not None else None
    halted = getattr(getattr(gate, "day", None), "halted", "") or getattr(getattr(gate, "week", None), "halted", "") or getattr(gate, "breaker", "")
    problems = [c for c in checks if c.status != "ok"]
    last_review = next((m for m in reversed(msgs) if m["from"] == "overseer"), None)

    agents = {
        "data": (status("data", data_problem),
                 (f"Prices for {len(data)} contracts, newest {max(v.index[-1] for v in data.values()).tz_convert(ET):%a %H:%M} ET"
                  if data else "Waiting for the first prices")),
        "news": (status("news", any(c.status != "ok" and "news" in c.name for c in checks)),
                 news_status.get("sentiment") or news_status.get("headlines") or "Watching the calendar and headlines"),
        "regime": (status("regime"), " · ".join(regimes[:4]) or "Needs a few hours of prices first"),
        "scanner": (status("scanner"), f"Watching {len(engine.strategies[0].setups) if engine else 0} setups; "
                    f"{len(ideas_today)} idea(s) today" if engine else "Waiting for prices"),
        "risk": ("paused" if halted else status("risk"), halted or (f"Limits OK; {len(open_pos)} of the 3 position slots in use")),
        "overseer": (status("overseer", off=not (reasoning_on and claude_ok)),
                     ("Off: no Claude connection" if not claude_ok else "Switched off in settings") if not (reasoning_on and claude_ok)
                     else (last_review["text"][:120] if last_review else "Waiting for an idea that passes every rule")),
        "execution": (status("execution"), "; ".join(f"{'long' if t.side == 1 else 'short'} {t.position} {t.plan.symbol}, stop {t.stop:.2f}"
                                                     for t in open_pos) or "No open positions"),
        "broker": ("off" if broker == "sim" else status("broker"),
                   "Simulated fills (paper money)" if broker == "sim" else broker),
        "watchdog": ("problem" if alerts else "working", f"{len(checks)} checks: " + (
            "all normal" if not problems else f"{len(problems)} need a look")),
        "learning": ("idle", f"Last review {str(learning.get('generated', ''))[:10]}: {len(learning.get('proposals', []))} suggestion(s)"
                     if learning else "First weekly review on Saturday"),
        "you": ("working" if any(m["to"] == "you" and m["fresh"] for m in msgs) else "idle",
                "Alerts by email" if alerts_by_email else "Alerts on the GitHub status page"),
    }
    # A few words for each name tag: the at-a-glance "what are you up to".
    to_you = sum(1 for m in msgs if m["to"] == "you" and m["fresh"])
    reviews_today = sum(1 for m in msgs if m["from"] == "overseer" and pd.Timestamp(m["ts"]).tz_convert(ET).date() == now.tz_convert(ET).date())
    short = {
        "data": (f"Prices in · {newest_px:%H:%M} ET" if (newest_px := (max(v.index[-1] for v in data.values()).tz_convert(ET) if data else None)) else "Waiting for prices"),
        "news": ("Mood: " + news_snapshot["risk_level"]) if news_snapshot else
                ("Quiet news" if news_status.get("headlines", "").endswith(" 0 new headlines") else "Reading headlines"),
        "regime": " · ".join(f"{r['symbol']} {r['trend'].replace('trending_', '').replace('_', ' ')}" for r in regime_list[:2]) or "Reading the weather",
        "scanner": f"{len(ideas_today)} idea{'s' if len(ideas_today) != 1 else ''} today",
        "risk": ("Paused: " + halted.split(":")[0].lower()) if halted else f"All clear · {len(open_pos)}/3 slots used",
        "overseer": ("Away: no Claude key" if not claude_ok else "Switched off") if not (reasoning_on and claude_ok) else f"{reviews_today} review{'s' if reviews_today != 1 else ''} today",
        "execution": ", ".join(f"{'long' if t.side == 1 else 'short'} {t.position} {t.plan.symbol}" for t in open_pos[:2]) or "Flat · no positions",
        "broker": "Simulated fills" if broker == "sim" else broker.split(" (")[0],
        "watchdog": "All normal" if not problems else f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to check",
        "learning": f"{len(learning.get('proposals', []))} suggestion(s)" if learning else "Next review Saturday",
        "you": f"{to_you} new alert{'s' if to_you != 1 else ''}" if to_you else "Inbox clear",
    }
    out = []
    for key, (name, group, job) in AGENTS.items():
        st, doing = agents[key]
        out.append({"id": key, "name": name, "group": group, "job": job, "status": st, "doing": doing, "short": short[key][:34],
                    "last": _ago(last_by_agent.get(key), now), "new": new_count_by_agent.get(key, 0)})
    return {"agents": out, "edges": [{"from": a, "to": b, "label": lbl} for a, b, lbl in EDGES], "messages": msgs[-80:],
            "market_open": market_open, "regimes": regime_list, "inbox": sum(1 for m in msgs if m["to"] == "you")}
