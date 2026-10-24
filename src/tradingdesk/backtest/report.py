"""Backtest reports in plain English: a Markdown summary (shown on the GitHub run page), an HTML page with the
equity curve, a CSV of every trade, and the full decision log."""

from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path

import pandas as pd

from tradingdesk.backtest.engine import BacktestResult
from tradingdesk.backtest.metrics import breakdown, equity_stats, summarize, trades_frame


def _money(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:,.0f}"


def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.0f}%"


def _num(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.{digits}f}"


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No trades._\n"
    cols = ["trades", "win rate", "avg win", "avg loss", "expectancy", "exp. R", "profit factor", "net P&L"]
    lines = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for k, r in df.iterrows():
        lines.append(f"| {k} | {int(r.trades)} | {_pct(r.win_rate)} | {_money(r.avg_win)} | {_money(r.avg_loss)} | {_money(r.expectancy)} | "
                     f"{_num(r.expectancy_r)} | {_num(r.profit_factor)} | {_money(r.net_pnl)} |")
    return "\n".join(lines) + "\n"


def verdict(s: dict, e: dict) -> str:
    """A short plain-English reading of the numbers."""
    if s.get("trades", 0) == 0:
        return "No trades were taken, so there is nothing to judge yet."
    parts = []
    if s["trades"] < 100:
        parts.append(f"Only {s['trades']} trades: too few to trust either way (we want 100+ per strategy).")
    if s["expectancy"] > 0 and s["profit_factor"] > 1.3:
        parts.append(f"Each trade made {_money(s['expectancy'])} on average after costs ({_num(s['expectancy_r'])}R).")
    elif s["expectancy"] > 0:
        parts.append("Slightly profitable after costs, but the edge is thin.")
    else:
        parts.append(f"Lost {_money(-s['expectancy'])} per trade on average after costs: no edge here.")
    if not math.isnan(s.get("payoff_ratio", math.nan)):
        parts.append(f"Winners averaged {_num(s['payoff_ratio'], 1)}x the size of losers with a {_pct(s['win_rate'])} win rate.")
    parts.append(f"The worst drop from a peak was {_money(-e['max_drawdown'])} ({abs(e['max_drawdown_pct']):.1f}%).")
    return " ".join(parts)


def build_markdown(res: BacktestResult, df: pd.DataFrame, s: dict, e: dict, data_note: str = "") -> str:
    md = [
        "# Backtest report",
        "",
        f"**Strategies:** {', '.join(res.strategies)} · **Contracts:** {', '.join(res.symbols)} · "
        f"**Period:** {res.start:%Y-%m-%d} to {res.end:%Y-%m-%d}",
        f"**Starting equity:** {_money(e['start_equity'])} · **Risk per trade:** {res.settings.risk.risk_per_trade_pct}%",
    ]
    if data_note:
        md += ["", f"> {data_note}"]
    md += [
        "",
        "## In plain English",
        "",
        verdict(s, e),
        "",
        "## Results",
        "",
        "| | |",
        "|---|---|",
        f"| Ending equity | {_money(e['end_equity'])} ({e['return_pct']:+.1f}%) |",
        f"| Trades | {s.get('trades', 0)} |",
    ]
    if s.get("trades"):
        md += [
            f"| Win rate | {_pct(s['win_rate'])} |",
            f"| Average win / average loss | {_money(s['avg_win'])} / {_money(s['avg_loss'])} |",
            f"| Expectancy per trade | {_money(s['expectancy'])} ({_num(s['expectancy_r'])}R) |",
            f"| Profit factor | {_num(s['profit_factor'])} |",
            f"| Max drawdown | {_money(-e['max_drawdown'])} ({abs(e['max_drawdown_pct']):.1f}%) |",
            f"| Sharpe ratio | {_num(e['sharpe'])} |",
            f"| Largest win / largest loss | {_money(s['largest_win'])} / {_money(s['largest_loss'])} |",
            f"| Longest losing streak | {s['longest_losing_streak']} trades |",
            f"| Commissions and roll costs | {_money(s['costs'])} |",
            f"| Stopped out, then target reached anyway | {_pct(s['stopped_then_target_pct'])} of stop-outs |",
        ]
        for title, col in [("By contract", "symbol"), ("By setup", "setup"), ("By market regime", "trend"), ("By volatility", "volatility"),
                           ("By session", "session"), ("By direction", "direction")]:
            if col in df and df[col].astype(str).str.len().gt(0).any():
                md += ["", f"## {title}", "", _table(breakdown(df, col))]
    risk = [d for d in res.decisions if d.agent == "risk" and d.action in ("rejected", "reduced", "alert", "halt")]
    if risk:
        counts: dict[tuple[str, str], int] = {}
        for d in risk:
            key = (d.action, d.reason.split(":")[0] if ":" in d.reason[:40] else d.reason.split(" (")[0][:60])
            counts[key] = counts.get(key, 0) + 1
        md += ["", "## Risk Manager", "", "| Action | Rule | Times |", "|---|---|---|"]
        md += [f"| {a} | {rule} | {n} |" for (a, rule), n in sorted(counts.items(), key=lambda kv: -kv[1])[:15]]
        alerts = [d for d in risk if d.action == "alert"]
        if alerts:
            md += ["", "Alerts that would have reached your phone:", ""] + [f"- {pd.Timestamp(d.ts):%Y-%m-%d %H:%M} UTC: {d.reason}" for d in alerts[:10]]
    return "\n".join(md) + "\n"


def _equity_png(equity: pd.Series) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 4), dpi=110)
    ax.plot(equity.index, equity.to_numpy(), color="#2b6cb0", lw=1.4)
    ax.fill_between(equity.index, equity.cummax().to_numpy(), equity.to_numpy(), color="#e53e3e", alpha=0.15, label="drawdown")
    ax.set_title("Equity")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def write_report(res: BacktestResult, out_dir: str | Path, data_note: str = "") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = trades_frame(res.trades)
    s = summarize(df)
    e = equity_stats(res.equity, res.settings.account.starting_equity)
    md = build_markdown(res, df, s, e, data_note)
    (out / "report.md").write_text(md)
    df.to_csv(out / "trades.csv", index=False)
    res.equity.rename("equity").to_csv(out / "equity.csv")
    with open(out / "decisions.jsonl", "w") as f:
        for d in res.decisions:
            f.write(json.dumps(d.to_dict(), default=str) + "\n")
    try:
        import markdown as mdlib  # optional

        body = mdlib.markdown(md, extensions=["tables"])
    except ImportError:
        body = "<pre>" + md.replace("<", "&lt;") + "</pre>"
    png = _equity_png(res.equity) if len(res.equity) > 1 else ""
    html = (
        "<!doctype html><meta charset='utf-8'><title>Backtest report</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1a202c}"
        "table{border-collapse:collapse}td,th{border:1px solid #e2e8f0;padding:4px 8px}img{max-width:100%}</style>"
        + (f"<img alt='Equity curve' src='data:image/png;base64,{png}'>" if png else "")
        + body
    )
    (out / "report.html").write_text(html)
    return {"summary": s, "equity": e, "markdown": md, "dir": str(out)}
