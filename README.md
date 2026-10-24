# Trading Desk

A team of software "agents" that trade US micro futures (MES, MNQ, MCL, MGC) together: one gathers data,
one reads the market, one finds setups, one acts as head trader, one manages risk and can veto anything,
one places orders, one watches system health, and one learns from every trade.

**Safety first:** the system paper trades (fake money) by default. Real-money trading only happens if you
change `mode: paper` to `mode: live` in the settings file yourself, and add your broker login. Nothing in
the code can switch it on for you.

## Where things stand

| Phase | What | Status |
|---|---|---|
| 1 | Data Agent and backtesting engine | ✅ built and tested |
| 2 | Scanner, Market Regime and Risk Manager agents (backtest only) | next |
| 3 | Execution Agent, paper trading on live data, Watchdog | |
| 4 | Head trader (Claude) and News & Sentiment agents | |
| 5 | Learning Agent and dashboard | |
| 6 | Cloud server, phone alerts, live-trading readiness | |

Right now the only strategy is `sanity_breakout`, a deliberately simple one used to check that the engine
works. Its results mean nothing for real trading. Real strategies arrive with the Scanner in Phase 2.

## Using it (no coding needed)

Everything runs on GitHub with buttons. You'll find them under the **Actions** tab of this repository.

### One-time setup: connect your Databento account

Databento supplies the historical market data. New accounts have come with free starter credit.

1. Sign up at [databento.com](https://databento.com) and copy your **API key** from the dashboard.
2. In this repository, go to **Settings → Secrets and variables → Actions → New repository secret**.
3. Name: `DATABENTO_API_KEY`. Secret: paste the key. Click **Add secret**.

The key is stored encrypted by GitHub. It never appears in the code or in any log.

### Download market data

1. **Actions → Download market data → Run workflow.**
2. Leave **Only show what it would cost** ticked the first time and click **Run workflow**.
   The run's page shows the price of the download. Nothing is downloaded or charged.
3. If you're happy with the cost, run it again with that box **unticked**.
   A safety limit (default $5 per contract) refuses anything more expensive.

The data is checked (duplicates, impossible prices, bad prints, gaps while the market was open), cleaned,
stitched across contract months, and stored with the repository's **market-data** release so backtests can
reuse it for free.

### Run a backtest

1. **Actions → Run backtest → Run workflow.**
2. Pick contracts, dates and starting equity, then **Run workflow**.
   (Tick **Use made-up test data** to try it before downloading anything.)
3. When it finishes (a few minutes), click the run. The report is on that page:
   results in plain English, win rate, average win vs. loss, expectancy, profit factor, drawdown, Sharpe,
   and breakdowns by contract, setup, session and direction.
4. At the bottom of the page under **Artifacts**, `backtest-report` has the full report with the equity chart
   (`report.html`), every trade (`trades.csv`) and every decision with its reason (`decisions.jsonl`).

## Settings

All limits live in [`config/default.yaml`](config/default.yaml), with a comment on every line. The main ones:

| Setting | Value | Meaning |
|---|---|---|
| `mode` | `paper` | paper / live / backtest |
| `account.starting_equity` | 50,000 | dollars |
| `risk.risk_per_trade_pct` | 1.5 | most a trade can lose, across all its entries |
| `risk.max_daily_loss_pct` | 4 | stop for the day (Phase 2) |
| `risk.max_consecutive_losses` | 3 | stop for the day (Phase 2) |
| `risk.max_weekly_drawdown_pct` | 8 | pause and alert (Phase 2) |
| `risk.min_reward_risk` | 3 | only take setups with at least 3:1 potential |
| `sessions.entry_windows` | all | trade any time a good setup appears |
| `costs.commission_per_side` | 0.85 | per contract, all fees included |

## How the engine trades (Phase 1)

- **Scaling in:** if a trade can be 3+ contracts, it enters about 1/3 on the signal and adds the rest only after a
  1-minute close confirms the move. The add is sized from whatever risk budget is left, so all entries together
  never risk more than 1.5%. If confirmation doesn't come within 10 minutes, the starter is closed.
  Smaller trades wait for the same confirmation and enter once.
- **A stop is in place from the first contract**, beyond real market structure plus an ATR buffer.
- **At 2R:** take 1/3 off and move the stop to breakeven. **Then** trail the rest with an ATR stop that only tightens.
- **One re-entry** if stopped out and price quickly reclaims the setup level.
- **Honest fills:** orders fill from the next minute, stops fill at the stop or worse (including gaps), limit orders
  need price to trade through them, slippage is larger overnight, thin minutes fill partially, and if the stop and
  the target are hit in the same minute the stop is assumed to come first.
- **Rolls:** follows the most-traded contract month; holding through a roll costs a round trip of fees and slippage.

More detail on the design choices: [`docs/design.md`](docs/design.md).

## For developers

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,databento]"
pytest                                   # 37 tests
desk backtest --synthetic --symbols MES --start 2024-01-01 --end 2024-04-01
desk data estimate --symbols MES --start 2024-01-01 --end 2025-01-01   # needs DATABENTO_API_KEY
docker compose run --rm desk backtest --synthetic --start 2024-01-01 --end 2024-04-01
```

Code layout: `src/tradingdesk/data` (Data Agent), `src/tradingdesk/backtest` (engine, simulated broker,
trade rules, metrics, reports), `src/tradingdesk/strategies`, `src/tradingdesk/core` (event bus and events).
Keys go in environment variables (see `.env.example`), never in code.

*This software is a research and automation tool, not financial advice. Futures trading carries substantial
risk of loss. Past or simulated performance does not guarantee future results.*
