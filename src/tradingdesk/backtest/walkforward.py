"""Walk-forward testing: tune on one stretch of history, then trade the NEXT stretch the settings have never seen.

    |---- train 6 months ----|-- test 3 months --|
                 |---- train 6 months ----|-- test 3 months --|
                              |---- train 6 months ----|-- test 3 months --|

In each training window every setup is tried with a deliberately small set of settings (`GRIDS`). The most
consistent version is kept, judged by a t-statistic of its R results (average R / spread x sqrt(trades)), which
rewards steady results over one lucky run. A setup must have at least `min_trades` trades and a t-statistic
above `min_t` in training, or it is switched off for the following test window. A small penalty for moving away
from the default settings guards against chasing noise.

Only the test windows count. They are chained together into one out-of-sample equity curve and compared with
the training results; a big gap between the two means the "edge" was mostly curve-fitting.
"""

from __future__ import annotations

import itertools
import logging
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingdesk.backtest.metrics import summarize, trades_frame
from tradingdesk.config import Settings

log = logging.getLogger(__name__)

# Small on purpose: a few meaningful alternatives per setup, not a search for the luckiest numbers.
GRIDS: dict[str, dict[str, list]] = {
    "trend_breakout": {"lookback": [20, 40], "target_atr": [5.0, 8.0]},
    "pullback": {"fast_ema": [20, 30]},
    "mean_reversion": {"band_width": [2.0, 2.5]},
    "liquidity_sweep": {"lookback": [30, 60]},
    "squeeze_breakout": {"squeeze_pct": [0.10, 0.20]},
}
WARMUP = pd.Timedelta(days=21)  # history loaded before each window so indicators are ready on day one
COMPLEXITY_PENALTY = 0.25       # t-statistic points per setting moved away from its default


@dataclass
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class WindowResult:
    window: Window
    chosen: dict[str, dict]                 # setup -> settings used in the test window
    train_stats: dict[str, dict]            # setup -> training summary of the chosen settings
    test_trades: pd.DataFrame
    test_equity: pd.Series
    skipped: dict[str, str] = field(default_factory=dict)  # setup -> why it was switched off


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    oos_trades: pd.DataFrame
    oos_equity: pd.Series
    in_sample: pd.DataFrame
    starting_equity: float


def make_windows(start, end, train_months: int = 6, test_months: int = 3) -> list[Window]:
    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    out, t = [], start
    while True:
        train_end = t + pd.DateOffset(months=train_months)
        test_end = min(train_end + pd.DateOffset(months=test_months), end)
        if train_end >= end or test_end <= train_end:
            break
        out.append(Window(t, train_end, train_end, test_end))
        t = t + pd.DateOffset(months=test_months)
    return out


def t_stat(r: pd.Series) -> float:
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(len(r)))


def _combos(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(grid[k] for k in keys))] or [{}]


def _run(task: tuple) -> tuple[pd.DataFrame, pd.Series]:
    """One backtest. Runs in a worker process, so it loads its own data from the local store."""
    settings_json, symbols, setups, load_from, trade_from, end, use_risk = task
    from tradingdesk.agents.risk import RiskManager
    from tradingdesk.agents.scanner import ScannerAgent
    from tradingdesk.backtest.engine import Backtester
    from tradingdesk.data.agent import DataAgent
    from tradingdesk.data.providers.synthetic import SyntheticProvider

    s = Settings.model_validate_json(settings_json)
    agent = DataAgent(s, provider=SyntheticProvider())  # provider unused: data comes from the store
    data = {}
    for sym in symbols:
        try:
            data[sym] = agent.continuous(sym, load_from, end)
        except LookupError:
            continue
    data = {k: v for k, v in data.items() if not v.empty}
    if not data:
        return pd.DataFrame(), pd.Series(dtype=float)
    gate = RiskManager(s) if use_risk else None
    res = Backtester(s, [ScannerAgent(s, setups)], data, risk_gate=gate, trade_from=trade_from).run()
    return trades_frame(res.trades), res.equity


class WalkForward:
    def __init__(self, settings: Settings, symbols: list[str], workers: int = 1, min_trades: int = 20, min_t: float = 1.0) -> None:
        # Research runs evaluate strategies, so the circuit breaker (which would end a run early) is off here.
        s = settings.model_copy(deep=True)
        s.risk.max_total_drawdown_pct = 0
        self.s = s
        self.symbols = symbols
        self.workers = max(1, workers)
        self.min_trades = min_trades
        self.min_t = min_t
        self.defaults = settings.scanner.enabled()

    def _map(self, tasks: list[tuple]) -> list[tuple[pd.DataFrame, pd.Series]]:
        if self.workers == 1 or len(tasks) == 1:
            return [_run(t) for t in tasks]
        with ProcessPoolExecutor(self.workers) as ex:
            return list(ex.map(_run, tasks))

    def run(self, start, end, train_months: int = 6, test_months: int = 3) -> WalkForwardResult:
        cfg = self.s.model_dump_json()
        wins = make_windows(start, end, train_months, test_months)
        if not wins:
            raise ValueError("The period is too short for even one train + test window.")
        results: list[WindowResult] = []
        in_sample_rows = []
        equity = self.s.account.starting_equity
        oos_trades, oos_eq = [], []
        for n, w in enumerate(wins, 1):
            log.info("Window %d/%d: train %s to %s, test %s to %s", n, len(wins), w.train_start.date(), w.train_end.date(), w.test_start.date(), w.test_end.date())
            # 1. Training: each setup on its own, each version of its settings.
            tasks, keys = [], []
            for setup, base in self.defaults.items():
                for combo in _combos(GRIDS.get(setup, {})):
                    params = {**base, **combo}
                    tasks.append((cfg, self.symbols, {setup: params}, w.train_start - WARMUP, w.train_start, w.train_end, True))
                    keys.append((setup, combo, params))
            outs = self._map(tasks)
            best: dict[str, tuple[float, dict, dict]] = {}
            for (setup, combo, params), (tdf, _) in zip(keys, outs, strict=True):
                r = tdf["r"] if not tdf.empty else pd.Series(dtype=float)
                moved = sum(1 for k, v in combo.items() if self.defaults[setup].get(k) != v)
                score = t_stat(r) - COMPLEXITY_PENALTY * moved
                stats = summarize(tdf) if not tdf.empty else {"trades": 0}
                stats["t_stat"] = t_stat(r)
                if setup not in best or score > best[setup][0]:
                    best[setup] = (score, params, stats)
            chosen, train_stats, skipped = {}, {}, {}
            for setup, (_score, params, stats) in best.items():
                train_stats[setup] = stats
                if stats.get("trades", 0) < self.min_trades:
                    skipped[setup] = f"only {stats.get('trades', 0)} training trades (need {self.min_trades})"
                elif stats["t_stat"] < self.min_t:
                    skipped[setup] = f"no reliable edge in training (t-stat {stats['t_stat']:.2f} < {self.min_t})"
                else:
                    chosen[setup] = params
                in_sample_rows.append({"window": n, "setup": setup, "used": setup in chosen,
                                       **{k: stats.get(k) for k in ("trades", "expectancy_r", "profit_factor", "t_stat")}})
            # 2. Testing: the chosen setups together, on data they have never seen.
            if chosen:
                s = self.s.model_copy(deep=True)
                s.account.starting_equity = equity
                tdf, teq = _run((s.model_dump_json(), self.symbols, chosen, w.test_start - WARMUP, w.test_start, w.test_end, True))
            else:
                tdf, teq = pd.DataFrame(), pd.Series({w.test_start: equity, w.test_end: equity}, dtype=float)
            if not teq.empty:
                teq = teq[teq.index >= w.test_start]
                equity = float(teq.iloc[-1]) if len(teq) else equity
            if not tdf.empty:
                tdf = tdf.assign(window=n)
                oos_trades.append(tdf)
            oos_eq.append(teq)
            results.append(WindowResult(w, chosen, train_stats, tdf, teq, skipped))
        trades = pd.concat(oos_trades, ignore_index=True) if oos_trades else pd.DataFrame()
        eq = pd.concat(oos_eq).sort_index() if oos_eq else pd.Series(dtype=float)
        eq = eq[~eq.index.duplicated(keep="last")]
        return WalkForwardResult(results, trades, eq, pd.DataFrame(in_sample_rows), self.s.account.starting_equity)


def report_markdown(res: WalkForwardResult, symbols: list[str], note: str = "") -> str:
    from tradingdesk.backtest.metrics import breakdown, equity_stats
    from tradingdesk.backtest.report import _money, _num, _pct, _table

    s = summarize(res.oos_trades) if not res.oos_trades.empty else {"trades": 0}
    e = equity_stats(res.oos_equity, res.starting_equity) if len(res.oos_equity) else {}
    used = res.in_sample[res.in_sample["used"]] if len(res.in_sample) else res.in_sample
    # Training expectancy of the setups that were actually used, weighted by their number of trades.
    ins_exp = float(np.average(used["expectancy_r"].astype(float), weights=used["trades"].astype(float))) if len(used) and used["trades"].sum() > 0 else float("nan")
    md = ["# Walk-forward test", "", f"**Contracts:** {', '.join(symbols)} · **Windows:** {len(res.windows)}"]
    if note:
        md += ["", f"> {note}"]
    md += ["", "## In plain English", ""]
    if s.get("trades", 0) == 0:
        md.append("No setup showed a reliable edge in training, so nothing was traded out of sample. That is the system protecting your money, not a failure.")
    else:
        verdict = "held up" if s["expectancy_r"] > 0 else "did not hold up"
        md.append(f"On data the settings had never seen, the chosen setups made {_num(s['expectancy_r'])}R per trade over {s['trades']} trades: the edge {verdict}. "
                  f"In training the same setups averaged {_num(ins_exp)}R per trade.")
        if not math.isnan(ins_exp) and ins_exp > 0:
            kept = s["expectancy_r"] / ins_exp
            if kept < 0.5:
                md.append(f" Only {max(kept, 0) * 100:.0f}% of the training edge survived on new data, so much of it was probably luck or curve-fitting.")
            else:
                md.append(f" About {min(kept, 9.99) * 100:.0f}% of the training edge survived on new data, a good sign the edge is real.")
    if e:
        md += ["", f"Out-of-sample equity: {_money(e['start_equity'])} → {_money(e['end_equity'])} ({e['return_pct']:+.1f}%), worst drop {abs(e['max_drawdown_pct']):.1f}%."]
    md += ["", "## Each window", "", "| # | Train | Test | Setups used (settings) | Switched off | Test trades | Test R/trade |", "|---|---|---|---|---|---|---|"]
    for i, w in enumerate(res.windows, 1):
        used = "; ".join(f"{k} {v}" for k, v in w.chosen.items()) or "none"
        off = "; ".join(f"{k}: {why}" for k, why in w.skipped.items()) or "—"
        tr = len(w.test_trades)
        rr = _num(float(w.test_trades["r"].mean())) if tr else "—"
        md.append(f"| {i} | {w.window.train_start:%Y-%m-%d} to {w.window.train_end:%Y-%m-%d} | {w.window.test_start:%Y-%m-%d} to {w.window.test_end:%Y-%m-%d} | {used} | {off} | {tr} | {rr} |")
    if s.get("trades"):
        md += ["", "## Out-of-sample results", "", "| | |", "|---|---|",
               f"| Trades | {s['trades']} |", f"| Win rate | {_pct(s['win_rate'])} |",
               f"| Expectancy | {_money(s['expectancy'])} ({_num(s['expectancy_r'])}R) |", f"| Profit factor | {_num(s['profit_factor'])} |"]
        md += ["", "### By setup (out of sample)", "", _table(breakdown(res.oos_trades, "setup"))]
    return "\n".join(md) + "\n"
