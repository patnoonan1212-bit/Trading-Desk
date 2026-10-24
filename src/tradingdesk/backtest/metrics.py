"""Performance statistics, overall and broken down by contract, setup, session and direction.

Definitions (all after commissions, slippage and roll costs):
  win rate        share of trades that made money
  avg win/loss    average dollars on winning / losing trades
  payoff ratio    avg win / avg loss (how big winners are compared with losers)
  expectancy      average dollars (and R) per trade; positive means the approach makes money on average
  profit factor   gross winnings / gross losses; above 1.0 is profitable, above 1.5 is good
  max drawdown    largest fall from a peak in the equity curve
  Sharpe          annualised average daily return / its volatility (risk-adjusted return)
  R               a trade's result divided by the dollars it risked; +3R = made three times the risk
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from tradingdesk.backtest.models import TradeRecord
from tradingdesk.contracts import spec


def trades_frame(trades: list[TradeRecord]) -> pd.DataFrame:
    rows = []
    for t in trades:
        pv = spec(t.symbol).point_value
        entries = [f for f in t.fills if f.purpose in ("entry", "add")]
        slip = sum(f.slippage_points * f.qty * pv for f in t.fills)
        rows.append(
            {
                "id": t.id, "symbol": t.symbol, "setup": t.setup, "direction": "long" if t.direction == 1 else "short",
                "session": t.session, "mode": t.mode, "reentry": t.is_reentry,
                "planned_at": t.planned_at, "opened_at": t.opened_at, "closed_at": t.closed_at,
                "contracts": t.max_contracts, "entries": len(entries),
                "avg_entry": (sum(f.price * f.qty for f in entries) / max(1, sum(f.qty for f in entries))) if entries else np.nan,
                "initial_stop": t.initial_stop, "target": t.target,
                "gross_pnl": round(t.gross_pnl, 2), "commissions": round(t.commissions, 2), "roll_costs": round(t.roll_costs, 2),
                "slippage_cost": round(slip, 2), "net_pnl": round(t.net_pnl, 2), "r": round(t.r_multiple(pv), 3),
                "mfe_r": round(t.max_favorable / t.risk_points, 2) if t.risk_points else 0.0,
                "mae_r": round(t.max_adverse / t.risk_points, 2) if t.risk_points else 0.0,
                "exit": "; ".join(t.exit_reasons), "stopped_then_target": t.stopped_then_target, "reason": t.reason,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["hold_minutes"] = (pd.to_datetime(df["closed_at"]) - pd.to_datetime(df["opened_at"])).dt.total_seconds() / 60
    return df


def _streak(wins: pd.Series) -> int:
    best = cur = 0
    for w in wins:
        cur = 0 if w else cur + 1
        best = max(best, cur)
    return best


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    pnl = df["net_pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    st = df["stopped_then_target"]
    stopped = st.notna()
    return {
        "trades": int(len(df)),
        "win_rate": float(len(wins) / len(df)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else math.nan,
        "expectancy": float(pnl.mean()),
        "expectancy_r": float(df["r"].mean()),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else math.inf,
        "net_pnl": float(pnl.sum()),
        "largest_win": float(pnl.max()),
        "largest_loss": float(pnl.min()),
        "longest_losing_streak": _streak(pnl > 0),
        "avg_hold_minutes": float(df["hold_minutes"].mean()),
        "costs": float(df["commissions"].sum() + df["roll_costs"].sum()),
        "stopped_then_target_pct": float(st[stopped].astype(bool).mean()) if stopped.any() else math.nan,
    }


def equity_stats(equity: pd.Series, starting: float) -> dict:
    if equity.empty:
        return {}
    peak = equity.cummax()
    dd = equity - peak
    daily = equity.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 2 and rets.std() > 0 else math.nan
    i = dd.idxmin()
    return {
        "start_equity": starting,
        "end_equity": float(equity.iloc[-1]),
        "return_pct": float((equity.iloc[-1] / starting - 1) * 100),
        "max_drawdown": float(dd.min()),
        "max_drawdown_pct": float((dd / peak).min() * 100),
        "max_drawdown_at": i,
        "sharpe": sharpe,
    }


def breakdown(df: pd.DataFrame, by: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = {k: summarize(g) for k, g in df.groupby(by)}
    out = pd.DataFrame(rows).T[["trades", "win_rate", "avg_win", "avg_loss", "expectancy", "expectancy_r", "profit_factor", "net_pnl"]]
    return out.sort_values("net_pnl", ascending=False)
