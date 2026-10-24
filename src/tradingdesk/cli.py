"""Command line: `desk <command>`. The GitHub Actions buttons call these same commands.

    desk data estimate  --symbols MES MNQ --start 2023-01-01 --end 2025-01-01
    desk data download  --symbols MES MNQ MCL MGC --start 2023-01-01 --end 2025-01-01
    desk data status
    desk backtest       --symbols MES MNQ MCL MGC --start 2024-01-01 --end 2025-01-01
    desk backtest       --synthetic ...   (made-up data, no subscription needed)
    desk walkforward    --start 2022-01-01 --end 2025-01-01
    desk live check     (is the free delayed price feed answering?)
    desk paper tick     (one paper-trading update: fetch prices, replay, Watchdog, status page)
    desk learn          --paper-state paper-state --out learning   (weekly learning review)
    desk learn-apply    --report learning/latest.json              (put passed suggestions into the settings file)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from tradingdesk.config import load_settings
from tradingdesk.core.bus import EventBus
from tradingdesk.logging_setup import attach_decision_log, setup_logging

log = logging.getLogger("desk")

STRATEGIES = {
    "scanner": "tradingdesk.agents.scanner:ScannerAgent",
    "sanity_breakout": "tradingdesk.strategies.sanity_breakout:SanityBreakout",
}


def _strategy(name: str, settings):
    module, cls = STRATEGIES[name].split(":")
    klass = getattr(__import__(module, fromlist=[cls]), cls)
    return klass(settings) if name == "scanner" else klass()


def _settings(args, **overrides):
    s = load_settings(args.config, **overrides)
    if getattr(args, "synthetic", False):
        s.data.provider = "synthetic"
        s.data.store_path = Path(str(s.data.store_path) + "-synthetic")
    return s


def _agent(settings, bus):
    from tradingdesk.data.agent import DataAgent

    return DataAgent(settings, bus=bus)


def cmd_estimate(args) -> int:
    s = _settings(args)
    agent = _agent(s, EventBus())
    total = 0.0
    for sym in args.symbols:
        cost = agent.estimate_cost(sym, args.start, args.end, args.schema)
        total += cost
        print(f"{sym}: ${cost:.2f}")
    print(f"Total: ${total:.2f}")
    return 0


def cmd_download(args) -> int:
    from tradingdesk.data.agent import CostLimitExceeded

    s = _settings(args)
    bus = EventBus()
    attach_decision_log(bus, s.logging.path)
    agent = _agent(s, bus)
    failed = False
    for sym in args.symbols:
        try:
            rep = agent.download(sym, args.start, args.end, args.schema, args.max_cost)
            print(rep.summary())
            agent.continuous(sym, rebuild=True)
            if rep.critical:
                failed = True
        except CostLimitExceeded as e:
            print(f"REFUSED: {e}")
            failed = True
    return 1 if failed else 0


def cmd_status(args) -> int:
    from tradingdesk.data.store import DataStore

    s = _settings(args)
    store = DataStore(s.data.store_path)
    for sym in s.contracts:
        cov = store.coverage(sym)
        print(f"{sym}: no data" if cov is None else f"{sym}: {cov[2]:,} bars from {cov[0]:%Y-%m-%d} to {cov[1]:%Y-%m-%d}")
    return 0


def cmd_backtest(args) -> int:
    from tradingdesk.backtest.engine import Backtester
    from tradingdesk.backtest.report import write_report

    s = _settings(args, mode="backtest")
    if args.equity:
        s.account.starting_equity = args.equity
    bus = EventBus()
    agent = _agent(s, bus)
    symbols = args.symbols or s.contracts
    if args.synthetic:
        for sym in symbols:
            if agent.store.coverage(sym) is None:
                agent.download(sym, args.start, args.end)
    data = {}
    for sym in symbols:
        try:
            data[sym] = agent.continuous(sym, args.start, args.end, rebuild=args.rebuild)
        except LookupError as e:
            print(f"Skipping {sym}: {e}")
    if not data:
        print("No data available. Download data first (or use --synthetic).")
        return 1
    strategies = [_strategy(n, s) for n in args.strategy]
    from tradingdesk.agents.risk import RiskManager

    gate = None if args.no_risk_manager else RiskManager(s)
    res = Backtester(s, strategies, data, bus=bus, risk_gate=gate).run()
    out = Path(args.out or f"reports/backtest-{datetime.now():%Y%m%d-%H%M%S}")
    note = "Made-up (synthetic) data: these results only show the machinery works and say nothing about real trading." if args.synthetic else ""
    rep = write_report(res, out, note)
    print(rep["markdown"])
    print(f"Report written to {out}/report.html")
    return 0


def cmd_walkforward(args) -> int:
    import os

    from tradingdesk.backtest.walkforward import WalkForward, report_markdown

    s = _settings(args, mode="backtest")
    symbols = args.symbols or s.contracts
    agent = _agent(s, EventBus())
    for sym in symbols:
        if args.synthetic and agent.store.coverage(sym) is None:
            agent.download(sym, args.start, args.end)
        try:
            agent.continuous(sym, rebuild=True)
        except LookupError as e:
            print(f"Skipping {sym}: {e}")
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    res = WalkForward(s, symbols, workers=workers, min_trades=args.min_trades).run(args.start, args.end, args.train_months, args.test_months)
    out = Path(args.out or f"reports/walkforward-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    note = "Made-up (synthetic) data: these results only show the machinery works." if args.synthetic else ""
    md = report_markdown(res, symbols, note)
    (out / "report.md").write_text(md)
    res.oos_trades.to_csv(out / "oos_trades.csv", index=False)
    res.in_sample.to_csv(out / "in_sample.csv", index=False)
    res.oos_equity.rename("equity").to_csv(out / "oos_equity.csv")
    print(md)
    return 0


def cmd_live_check(args) -> int:
    """Ask the free live feed for the last few hours of each contract and say, in plain English, what came back."""
    import pandas as pd

    from tradingdesk.data.providers.yahoo import YahooProvider, yahoo_symbol
    from tradingdesk.data.symbols import active_contracts

    s = _settings(args)
    yp = YahooProvider()
    now = pd.Timestamp.now(tz="UTC")
    lines = ["## Free live data feed check", "", f"Checked at {now:%Y-%m-%d %H:%M} UTC.", "",
             "| Contract | Source | Bars (last 2 days) | Latest complete minute | Minutes behind | Last price | Minutes with volume |",
             "|---|---|---|---|---|---|---|"]
    ok = 0
    for root in args.symbols or s.contracts:
        try:
            bars = yp.fetch_bars(root, now - pd.Timedelta(days=2), now)
        except Exception as e:  # report every contract even if one fails
            lines.append(f"| {root} | error | | | | | {str(e)[:80]} |")
            continue
        if bars.empty:
            lines.append(f"| {root} | nothing returned ({', '.join(yahoo_symbol(c, root) for c in active_contracts(root, now))}, {root}=F) | 0 | | | | |")
            continue
        ok += 1
        for sym, g in bars.groupby("symbol"):
            last = g.iloc[-1]
            src = f"{root}=F (rolling)" if root in yp.used_fallback else yahoo_symbol(sym, root)
            lines.append(f"| {sym} | {src} | {len(g):,} | {last.ts:%a %H:%M} UTC | {(now - last.ts).total_seconds() / 60:.0f} | {last.close:g} | "
                         f"{(g.volume > 0).mean():.0%} |")
    lines += ["", f"**{ok} of {len(args.symbols or s.contracts)} contracts returned data.** "
              "Prices on this free feed are about 10 minutes behind; during the weekend and the daily 5-6 pm ET break no new bars arrive."]
    lines += ["", "| Source | Result |", "|---|---|"]
    from tradingdesk.agents.news import _source, http_get, parse_feed, parse_ff_calendar

    try:
        cal = parse_ff_calendar(http_get(s.news.calendar_url))
        lines.append(f"| Calendar | {len(cal)} high-impact US releases this week |")
    except Exception as e:
        lines.append(f"| Calendar | error: {str(e)[:80]} |")
    for url in s.news.feeds:
        try:
            items = parse_feed(http_get(url), _source(url))
            newest = max((h["published"] for h in items), default="")
            lines.append(f"| News {_source(url)} | {len(items)} headlines, newest {newest[:16].replace('T', ' ')} UTC |")
        except Exception as e:
            lines.append(f"| News {_source(url)} ({url.split('/')[-1][:20]}) | error: {str(e)[:80]} |")
    md = "\n".join(lines) + "\n"
    print(md)
    if args.out:
        Path(args.out).write_text(md)
    return 0


def cmd_paper_tick(args) -> int:
    from tradingdesk.live.paper import PaperTrader

    s = _settings(args, mode="paper")
    if args.state:
        s.paper.state_path = Path(args.state)
    res = PaperTrader(s).tick(args.now)
    print(res.status_md)
    if res.notify_md:
        print(res.notify_md)
    return 0


def cmd_learn(args) -> int:
    from tradingdesk.agents.learning import LearningAgent, report_markdown, write

    s = _settings(args, mode="backtest")
    history = None
    if not args.no_history:
        agent = _agent(s, EventBus())
        history = {}
        for sym in s.contracts:
            if args.synthetic and agent.store.coverage(sym) is None and args.start:
                agent.download(sym, args.start, args.end)
            try:
                history[sym] = agent.continuous(sym, args.start, args.end, rebuild=args.synthetic)
            except LookupError:
                pass
        history = {k: v for k, v in history.items() if len(v)} or None
    rep = LearningAgent(s, args.min_trades).review(history, args.paper_state)
    write(rep, args.out)
    print(report_markdown(rep))
    return 0


def cmd_learn_apply(args) -> int:
    """Apply the suggestions that passed their test and still match the current settings. Writes the PR text."""
    from tradingdesk.agents.learning import changes_unchanged, load_proposals
    from tradingdesk.config import DEFAULT_CONFIG
    from tradingdesk.config_edit import apply_to_file

    path = Path(args.config or DEFAULT_CONFIG)
    s = load_settings(path)
    chosen = [p for p in load_proposals(args.report) if p.status == "passed" and (not args.ids or p.id in args.ids) and changes_unchanged(s, p)]
    body = ["The Learning Agent suggests these setting changes. Each one was tested on market history that was not used "
            "to find it, and improved the result there.", "",
            "**Merge** this pull request to accept them, or **close** it to leave the settings as they are.", ""]
    for p in chosen:
        lines = apply_to_file(path, p.changes)
        body += [f"### {p.id}: {p.title}", "", p.why, "", f"- Evidence: {p.evidence}", f"- Test: {p.test}"]
        body += [f"- `{x}`" for x in lines] + [""]
    body += ["The full learning review is in `learning/report.md`."]
    Path(args.body).write_text("\n".join(body) + "\n")
    print(f"{len(chosen)} suggestion(s) applied to {path}")
    return 0 if chosen else 3


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="desk", description="Trading desk command line")
    p.add_argument("--config", default=None, help="settings file (default: config/default.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, symbols_required=True):
        sp.add_argument("--symbols", nargs="+", required=symbols_required, help="e.g. MES MNQ MCL MGC")
        sp.add_argument("--start", required=True)
        sp.add_argument("--end", required=True)
        sp.add_argument("--synthetic", action="store_true", help="use made-up data instead of a real provider")

    d = sub.add_parser("data", help="download and inspect market data")
    dsub = d.add_subparsers(dest="data_cmd", required=True)
    e = dsub.add_parser("estimate", help="what a download would cost")
    common(e)
    e.add_argument("--schema", default=None)
    e.set_defaults(func=cmd_estimate)
    dl = dsub.add_parser("download", help="download, check and store data")
    common(dl)
    dl.add_argument("--schema", default=None)
    dl.add_argument("--max-cost", type=float, default=None, help="refuse downloads above this many dollars")
    dl.set_defaults(func=cmd_download)
    st = dsub.add_parser("status", help="what data is stored")
    st.add_argument("--synthetic", action="store_true")
    st.set_defaults(func=cmd_status)

    b = sub.add_parser("backtest", help="run a backtest and write a report")
    common(b, symbols_required=False)
    b.add_argument("--strategy", nargs="+", default=["scanner"], choices=sorted(STRATEGIES))
    b.add_argument("--equity", type=float, default=None, help="starting equity (default from config)")
    b.add_argument("--out", default=None, help="folder for the report")
    b.add_argument("--rebuild", action="store_true", help="rebuild the continuous contracts from stored bars")
    b.add_argument("--no-risk-manager", action="store_true", help="turn off portfolio risk limits (for comparison only)")
    b.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walkforward", help="tune on past data, test on the next stretch, roll forward")
    common(wf, symbols_required=False)
    wf.add_argument("--train-months", type=int, default=6)
    wf.add_argument("--test-months", type=int, default=3)
    wf.add_argument("--min-trades", type=int, default=20, help="training trades a setup needs before it may be used")
    wf.add_argument("--workers", type=int, default=0, help="parallel backtests (default: CPUs - 1)")
    wf.add_argument("--out", default=None)
    wf.set_defaults(func=cmd_walkforward)

    lv = sub.add_parser("live", help="the free delayed live data feed")
    lsub = lv.add_subparsers(dest="live_cmd", required=True)
    lc = lsub.add_parser("check", help="fetch recent bars and report what came back")
    lc.add_argument("--symbols", nargs="+", default=None)
    lc.add_argument("--out", default=None, help="also write the report to this file")
    lc.set_defaults(func=cmd_live_check)

    pp = sub.add_parser("paper", help="paper trading on live prices")
    psub = pp.add_subparsers(dest="paper_cmd", required=True)
    pt = psub.add_parser("tick", help="one update: fetch prices, replay the desk, run the Watchdog, write the status page")
    pt.add_argument("--state", default=None, help="folder for the paper account (default from config)")
    pt.add_argument("--now", default=None, help=argparse.SUPPRESS)
    pt.set_defaults(func=cmd_paper_tick)

    le = sub.add_parser("learn", help="weekly learning review: what works, what doesn't, suggested changes")
    le.add_argument("--paper-state", default=None, help="paper trading folder to learn from")
    le.add_argument("--out", default="learning")
    le.add_argument("--min-trades", type=int, default=30)
    le.add_argument("--start", default=None)
    le.add_argument("--end", default=None)
    le.add_argument("--no-history", action="store_true", help="don't use downloaded market history")
    le.add_argument("--synthetic", action="store_true")
    le.set_defaults(func=cmd_learn)
    la = sub.add_parser("learn-apply", help="apply suggestions that passed their test to the settings file")
    la.add_argument("--report", default="learning/latest.json")
    la.add_argument("--ids", nargs="*", default=None)
    la.add_argument("--body", default="pr-body.md", help="where to write the pull request text")
    la.set_defaults(func=cmd_learn_apply)

    args = p.parse_args(argv)
    setup_logging("INFO", None)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
