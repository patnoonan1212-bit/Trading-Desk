"""Command line: `desk <command>`. The GitHub Actions buttons call these same commands.

    desk data estimate  --symbols MES MNQ --start 2023-01-01 --end 2025-01-01
    desk data download  --symbols MES MNQ MCL MGC --start 2023-01-01 --end 2025-01-01
    desk data status
    desk backtest       --symbols MES MNQ MCL MGC --start 2024-01-01 --end 2025-01-01
    desk backtest       --synthetic ...   (made-up data, no subscription needed)
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

STRATEGIES = {"sanity_breakout": "tradingdesk.strategies.sanity_breakout:SanityBreakout"}


def _strategy(name: str):
    module, cls = STRATEGIES[name].split(":")
    return getattr(__import__(module, fromlist=[cls]), cls)()


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
    strategies = [_strategy(n) for n in args.strategy]
    res = Backtester(s, strategies, data, bus=bus).run()
    out = Path(args.out or f"reports/backtest-{datetime.now():%Y%m%d-%H%M%S}")
    note = "Made-up (synthetic) data: these results only show the machinery works and say nothing about real trading." if args.synthetic else ""
    rep = write_report(res, out, note)
    print(rep["markdown"])
    print(f"Report written to {out}/report.html")
    return 0


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
    b.add_argument("--strategy", nargs="+", default=["sanity_breakout"], choices=sorted(STRATEGIES))
    b.add_argument("--equity", type=float, default=None, help="starting equity (default from config)")
    b.add_argument("--out", default=None, help="folder for the report")
    b.add_argument("--rebuild", action="store_true", help="rebuild the continuous contracts from stored bars")
    b.set_defaults(func=cmd_backtest)

    args = p.parse_args(argv)
    setup_logging("INFO", None)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
