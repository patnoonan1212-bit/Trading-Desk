"""The live-readiness checklist: what must be true before real money is even possible.

Every item is measured from the desk's own records, not ticked by hand. Live mode (`mode: live`) refuses to send a
single real order until every item passes; until then the broker side stays in dry run and you are alerted. Passing
the checklist doesn't switch anything on: going live is still your decision, made in the settings file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from tradingdesk.config import Settings

MIN_WEEKS = 8
MIN_TRADES = 50
MIN_PROFIT_FACTOR = 1.2
MAX_DRAWDOWN_PCT = 10.0
CLEAN_DAYS = 14
DEMO_DAYS = 14
MAX_RUN_GAP_MIN = 2.0


@dataclass
class Item:
    name: str
    passed: bool
    detail: str


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def evaluate(s: Settings, state_dir: str | Path, now: pd.Timestamp, email_ready: bool, state: dict | None = None) -> list[Item]:
    d = Path(state_dir)
    st = state if state is not None else _read_json(d / "state.json")
    items: list[Item] = []

    started = pd.Timestamp(st["started_at"]) if st.get("started_at") else None
    weeks = (now - started).days / 7 if started is not None else 0.0
    trades = pd.read_csv(d / "trades.csv") if (d / "trades.csv").exists() and (d / "trades.csv").stat().st_size > 2 else pd.DataFrame()
    n = len(trades)
    items.append(Item("Paper track record", weeks >= MIN_WEEKS and n >= MIN_TRADES,
                      f"{weeks:.1f} of {MIN_WEEKS} weeks, {n} of {MIN_TRADES} closed trades"))

    if n:
        wins, losses = trades.loc[trades.net_pnl > 0, "net_pnl"].sum(), -trades.loc[trades.net_pnl <= 0, "net_pnl"].sum()
        pf = wins / losses if losses > 0 else float("inf")
        exp_r = float(trades["r"].mean())
        items.append(Item("Paper results profitable", n >= MIN_TRADES and exp_r > 0 and pf >= MIN_PROFIT_FACTOR,
                          f"average {exp_r:+.2f}R per trade, profit factor {pf:.2f} (need above 0R and {MIN_PROFIT_FACTOR})"))
    else:
        items.append(Item("Paper results profitable", False, "no closed paper trades yet"))

    eq_p = d / "equity.csv"
    if eq_p.exists() and eq_p.stat().st_size > 10:
        eq = pd.read_csv(eq_p, index_col=0)["equity"].astype(float)
        dd = float(((eq.cummax() - eq) / eq.cummax()).max() * 100) if len(eq) else 0.0
        items.append(Item("Paper drawdown contained", dd <= MAX_DRAWDOWN_PCT and n >= MIN_TRADES,
                          f"worst drop {dd:.1f}% (limit {MAX_DRAWDOWN_PCT:.0f}%)"))
    else:
        items.append(Item("Paper drawdown contained", False, "no equity history yet"))

    lj = _read_json(Path(s.paper.learning_file))
    base = lj.get("baseline") or {}
    hist = lj.get("test_from") is not None and bool(base)
    items.append(Item("Edge holds on real history", hist and base.get("total_r", 0) > 0,
                      f"held-back history: {base.get('trades', 0)} trades, {base.get('total_r', 0):+.1f}R" if hist
                      else "needs downloaded market history and a learning review"))

    alerts = [pd.Timestamp(t) for t in st.get("alert_times", [])]
    recent = [t for t in alerts if t >= now - pd.Timedelta(days=CLEAN_DAYS)]
    items.append(Item("No system alerts for two weeks", weeks * 7 >= CLEAN_DAYS and not recent,
                      f"{len(recent)} update(s) with alerts in the last {CLEAN_DAYS} days" if recent else
                      ("clean" if weeks * 7 >= CLEAN_DAYS else "not enough history yet")))

    m = _read_json(d / "mirror.json")
    since = pd.Timestamp(m["real_since"]) if m.get("real_since") else None
    last_problem = pd.Timestamp(m["last_problem"]) if m.get("last_problem") else None
    demo_days = (now - since).days if since is not None else 0
    demo_ok = since is not None and demo_days >= DEMO_DAYS and (last_problem is None or last_problem < now - pd.Timedelta(days=DEMO_DAYS))
    items.append(Item("Two clean weeks mirroring on Tradovate demo", demo_ok,
                      f"{demo_days} of {DEMO_DAYS} days with real demo orders" + (f", last problem {last_problem:%Y-%m-%d}" if last_problem is not None else "")
                      if since is not None else "not started (needs a Tradovate demo account, broker.name: tradovate, dry_run: false)"))

    items.append(Item("Real-time prices", s.paper.data_provider == "tradovate",
                      "Tradovate real-time" if s.paper.data_provider == "tradovate" else f"currently {s.paper.data_provider} (about 10 minutes delayed)"))

    runs = sorted(pd.Timestamp(t) for t in st.get("run_times", []))
    runs = [t for t in runs if t >= now - pd.Timedelta(days=1)]
    gap = float(np.median(np.diff([t.value for t in runs])) / 6e10) if len(runs) > 10 else float("inf")
    items.append(Item("Runs every minute on its own server", gap <= MAX_RUN_GAP_MIN,
                      f"typical gap between updates {gap:.0f} min" if gap != float("inf") else "not enough recent updates (GitHub runs every 15 minutes)"))

    items.append(Item("Email alerts working", email_ready, "set up" if email_ready else "add the ALERT_EMAIL_TO, SMTP_USERNAME and SMTP_PASSWORD secrets"))
    items.append(Item("Kill switch off", not s.risk.kill_switch, "off" if not s.risk.kill_switch else "on"))
    return items


def ready(items: list[Item]) -> bool:
    return all(i.passed for i in items)


def markdown(items: list[Item]) -> str:
    done = sum(i.passed for i in items)
    md = [f"**{done} of {len(items)} passed.** Live trading stays impossible until all pass, and then it is still your call.", "",
          "| | Check | Where it stands |", "|---|---|---|"]
    md += [f"| {'✅' if i.passed else '⬜'} | {i.name} | {i.detail} |" for i in items]
    return "\n".join(md) + "\n"
