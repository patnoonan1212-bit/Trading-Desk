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
| 2 | Scanner, Market Regime and Risk Manager agents (backtest only) | ✅ built and tested |
| 3 | Execution Agent, paper trading on live data, Watchdog | ✅ built and tested |
| 4 | Head trader (Claude) and News & Sentiment agents | ✅ built and tested |
| 5 | Learning Agent and dashboard | ✅ built and tested |
| 6 | Email alerts, Tradovate connection, live-readiness checklist, server-ready | ✅ built and tested (Tradovate itself not yet tried on a real account) |

The backtests now use the real decision chain: the **Market Regime Agent** reads the market, the **Scanner**
finds setups that suit it, and the **Risk Manager** can approve, shrink or veto every trade. Nothing has been
tested on real prices yet: that needs the Databento download below.

**Paper trading is running** on free prices that are about 10 minutes delayed (from Yahoo Finance). That is
enough to prove the whole desk works on fresh prices without paying for data. Real-time prices come through
Tradovate later, before any decision about live money.

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
reuse it for free. Because this repository is public and the data is licensed for your use only, the stored
file is encrypted with your Databento key.

### Run a backtest

1. **Actions → Run backtest → Run workflow.**
2. Pick contracts, dates and starting equity, then **Run workflow**.
   (Tick **Use made-up test data** to try it before downloading anything.)
3. When it finishes (a few minutes), click the run. The report is on that page:
   results in plain English, win rate, average win vs. loss, expectancy, profit factor, drawdown, Sharpe,
   and breakdowns by contract, setup, session and direction.
4. At the bottom of the page under **Artifacts**, `backtest-report` has the full report with the equity chart
   (`report.html`), every trade (`trades.csv`) and every decision with its reason (`decisions.jsonl`).

### Walk-forward test (is the edge real?)

A normal backtest can look great simply because the settings were tuned on the same data. The walk-forward test
guards against that:

1. **Actions → Walk-forward test → Run workflow.**
2. It tunes the setups on a stretch of history (default 12 months), then trades the *next* stretch (default
   3 months) with those settings, untouched. It slides forward and repeats until the data runs out.
3. Setups that didn't earn their keep in training (too few trades, or results that could easily be luck) are
   switched off for the next stretch.
4. The report shows only the results from the unseen stretches, joined together. That is the honest estimate.

## Paper trading (Phase 3)

The desk paper trades by itself, with pretend money and the same rules as the backtests.

- **Where to look:** the **Issues** tab has an issue called **📈 Paper trading desk**. Its first post is the live
  status page: equity, open positions, results so far, the Watchdog's health checks and the latest decisions. It
  updates every 15 minutes while the market is open (Sunday 6 pm to Friday 5 pm New York time).
- **News comes to you:** new trades, closed trades (with profit or loss) and alerts are posted as comments on that
  issue. GitHub then notifies you by email and in the GitHub mobile app. To get alerts only, set `paper.notify`
  to `alerts` in the settings file; `none` turns them off.
- **Stop new trades:** set `risk.kill_switch: true` in [`config/default.yaml`](config/default.yaml) (edit the file
  on GitHub with the pencil icon and click **Commit changes**). Open trades are still managed and protected.
- **Stop paper trading entirely:** **Actions → Paper trading → ⋯ → Disable workflow.**
- **Start over:** **Actions → Paper trading → Run workflow**, tick **Start fresh**.
- **Is the free price feed working?** **Actions → Check live data → Run workflow** shows the latest price of each
  contract and how far behind it is.

**The Watchdog** checks after every update that prices are arriving on time, there are no holes in the data,
updates aren't being missed, every open position has its stop in place, no trade carries more risk than it was
given, and replaying the recorded prices still gives the same decisions. Anything wrong shows on the status page,
and serious problems are posted to you.

Good to know:
- GitHub's scheduled runs are sometimes late by several minutes. No trades are lost when that happens: each
  update replays every recorded price in order, so the paper results are exactly what the desk would have done.
  Only the notifications arrive later.
- GitHub pauses scheduled runs in a repository with no activity for 60 days. Phase 6 moves the desk to its own
  server.
- This repository is public, so the status issue is visible to anyone. It only shows paper trades.

## The head trader and the news desk (Phase 4)

**Head trader.** Every trade idea that passes the Scanner and all of the Risk Manager's checks goes to Claude
before it is placed. Claude sees what a human head trader would: the setup and why the Scanner likes it, the last
few hours of the chart, the market regime, the account (equity, today's result, open trades, recent trades),
scheduled releases coming up, and the latest news. It then approves the trade, makes it smaller, or passes on it,
and says why in a sentence or two. It can never make a trade bigger, move a stop, or overrule the Risk Manager.
Its comments appear on the status page and in the trade notifications.

**News & Sentiment.** Every update the News Agent:
- adds this week's important US releases (inflation, jobs, Fed, GDP, retail sales and more) to the no-trade
  windows around news, from a free live calendar. The weekly crude oil inventory report pauses crude (MCL) only.
- reads free headline feeds (the Federal Reserve, MarketWatch, CNBC, OilPrice).
- about once an hour, has Claude read the new headlines and record the mood for each market. If something
  serious and unexpected is breaking (a surprise Fed move, a war, a crash), it can pause new trades for up to an hour.

**Sudden-move guard.** Separately from Claude, if any contract moves in one minute many times more than it
normally does, which is often the first sign of unexpected news, new trades on it pause for 15 minutes. This rule
also runs in backtests.

**Turning Claude on.** It needs an Anthropic API key (Claude is paid per use):
1. Create a key at [console.anthropic.com](https://console.anthropic.com) (**API keys → Create key**). While
   there, set a monthly spend limit under **Billing** as a second safety net.
2. In this repository: **Settings → Secrets and variables → Actions → New repository secret**, name
   `ANTHROPIC_API_KEY`, paste the key.

The desk caps its own Claude spending at **$20 a month** (`reasoning.monthly_budget_usd`). At current prices
that should cover a few trade reviews a day plus hourly news reading, roughly $5 to $15 a month in total. The status page
shows what's been spent. When the cap is reached, or without a key, the desk keeps trading on its rules alone
(set `reasoning.when_unavailable: skip` to take no trades instead).

Claude is not used in backtests: it already knows how past markets turned out, so its reviews of old trades
would look better than they could ever be in real time. Its value can only be judged in paper trading, by
comparing the trades it approved with the ones it passed on (that comparison comes with the Learning Agent in Phase 5).

## How big is each trade?

1. **Risk budget:** a trade may lose at most **1.5% of the account** if its stop is hit (about $750 on
   $50,000), counting every entry plus fees and expected slippage.
2. **Contracts:** that budget divided by what one contract would lose at the stop, rounded down. For example,
   an MES stop 10 points away risks about $54 a contract, so 13 contracts.
3. **Only ever smaller, never bigger:**
   - **Drawdown brake:** 10% or more below the account's high, every trade risks half (0.75%) until it recovers.
   - **MES and MNQ** share one 2% budget.
   - **The head trader** can cut a trade to 25-75%.
   - **Score-based sizing** (off until the Learning Agent shows higher scores really do better): setups scoring
     50-64 risk 0.75%, 65-79 risk 1.1%, 80+ risk the full 1.5%.
4. **Positions grow and shrink with the account**, because the budget is a percentage of it.

Other protections: everything is **closed 30 minutes before Friday's close** (weekend news can make prices open
far beyond a stop), and there are no new entries in that window. A **stale-trade exit** (close trades that have
gone nowhere after a set time) is built but switched off until tested.

## Dashboard and the Learning Agent (Phase 5)

**Dashboard.** A web page with everything in one place: equity and the worst drop so far, open positions, recent
trades with the head trader's call on each, results by setup, the news, system health and what the desk is learning.
It works on a phone and follows your phone's light or dark mode. It is rebuilt every update and published about
once an hour.

One-time switch: **Settings → Pages → Build and deployment → Source: GitHub Actions**. The address is then
`https://patnoonan1212-bit.github.io/Trading-Desk/`. Like the status issue it is public and shows only paper trades.

**Learning Agent.** Every Saturday (or **Actions → Learning review → Run workflow**) it studies every closed trade:
- results by setup, market regime, volatility, session, hour, weekday, direction and Scanner score, each with a
  plain verdict. A group needs 30+ trades before it counts at all.
- exits: how often trades ran to 1R and 2R, and how often a stopped-out trade reached its target anyway.
- the head trader: replays the paper period with the rules alone and shows whether Claude's calls added money.

It then suggests setting changes, such as switching off a losing setup, not trading a setup in a regime where it loses,
raising the minimum score, or giving stops more room. **Each suggestion is tested on market history that was not used to find
it**, and only those that improve the result there are offered. Those go into a **pull request**
(the Pull requests tab): it shows the exact lines it would change in the settings file and the evidence. Press
**Merge** to accept or **Close** to decline. It never touches risk limits and never changes anything on its own.

Until market history is downloaded (the Databento step), suggestions can't be tested, so the review only
reports what it sees. The review is saved in [`learning/report.md`](learning/report.md) and posted on the
paper trading issue.

If GitHub won't let the workflow open the pull request itself, the run page gives you a link that opens it with one
click. To let it open them directly: **Settings → Actions → General → Allow GitHub Actions to create and approve pull
requests**.

## Alerts, Tradovate and the road to live trading (Phase 6)

### Email alerts

New trades, closed trades, limit hits and system problems are emailed to you, plus a summary after each trading
day (5 pm New York time). Without email set up they keep going to the status issue as before.

1. Use a Gmail address to send from (yours is fine). It needs 2-step verification switched on
   (myaccount.google.com → Security).
2. Create an **app password**: myaccount.google.com → search "App passwords" → name it "Trading desk" →
   **Create**. Copy the 16-letter password it shows.
3. In this repository: **Settings → Secrets and variables → Actions → New repository secret**, three times:
   - `ALERT_EMAIL_TO`: the address alerts should go to
   - `SMTP_USERNAME`: the Gmail address from step 1
   - `SMTP_PASSWORD`: the app password from step 2
4. **Actions → Send test email → Run workflow.** You should get a test email within a minute.

### Connecting Tradovate (when you have an account)

The desk can mirror every trade at Tradovate: same contracts and sizes, with a **real stop order held at the broker**
so positions stay protected even if the desk goes offline. A separate safety net watches the real account's value
and closes everything if it is down more than the daily limit. Start with a **demo** (practice money) account.

1. Open a Tradovate demo account, then get API access in Tradovate (**Settings → API Access**; Tradovate may
   charge for API access and for market data, so check their current terms). Create an API key and note its
   **client id (cid)** and **secret**.
2. Add these repository secrets: `TRADOVATE_USERNAME`, `TRADOVATE_PASSWORD`, `TRADOVATE_CID`, `TRADOVATE_SECRET`
   (and optionally `TRADOVATE_APP_ID`, `TRADOVATE_APP_VERSION`).
3. **Actions → Check Tradovate → Run workflow** (demo). It only reads: login, accounts, balance, positions, orders,
   contracts and real-time prices. Nothing is placed or cancelled.
4. In `config/default.yaml` set `broker.name: tradovate`. With `dry_run: true` (the default) it writes down every
   order it *would* send, on the status page and in alerts, and sends nothing. You can do this today, before having
   an account, to see exactly what it would do.
5. When that looks right, set `dry_run: false` to send real orders to the **demo** account.

### Ready for real money?

The status page and dashboard show a **live-readiness checklist**, measured from the desk's own records:
- **Track record:** 8+ weeks and 50+ trades of paper trading.
- **Profitable paper results:** average above 0R per trade, profit factor at least 1.2.
- **Drawdown:** the worst drop is under 10%.
- **Real history:** the edge holds on held-back market history.
- **System health:** two weeks with no system alerts.
- **Demo broker:** two clean weeks mirroring on a Tradovate demo account.
- **Real-time prices.**
- **Its own server,** updating every minute.
- **Email alerts** working.
- **Kill switch** off.

Live mode stays locked until every item passes: real orders are refused and you're alerted. Even then nothing goes
live by itself. Going live means you setting, in the settings file, `mode: live`, `broker.environment: live`,
`broker.dry_run: false` and `paper.data_provider: tradovate`, with your live Tradovate secrets.

### Moving to a server (later, before live money)

GitHub runs the desk every 15 minutes, which is fine for paper trading. Live trading needs an update every minute,
which means a small always-on server, about $5 a month. The desk is packaged for it: the `Dockerfile` runs
`desk run`, one update a minute, using the same secrets as environment variables and a persistent folder
for the paper account. Railway or Render can deploy it straight from this repository; ask and it will be walked
through step by step.

## Settings

All limits live in [`config/default.yaml`](config/default.yaml), with a comment on every line. The main ones:

| Setting | Value | Meaning |
|---|---|---|
| `mode` | `paper` | paper / live / backtest |
| `account.starting_equity` | 50,000 | dollars |
| `risk.risk_per_trade_pct` | 1.5 | most a trade can lose, across all its entries |
| `risk.max_daily_loss_pct` | 4 | stop new trades for the day |
| `risk.max_consecutive_losses` | 3 | stop new trades for the day |
| `risk.max_weekly_drawdown_pct` | 8 | pause until next week and alert |
| `risk.max_total_drawdown_pct` | 20 | circuit breaker: stop everything until you restart it (0 = off) |
| `risk.correlated_groups` | MES + MNQ ≤ 2% | the two stock-index contracts share one risk budget |
| `risk.news_blackout_minutes` | 10 | no new entries 10 minutes either side of jobs reports and Fed decisions |
| `risk.kill_switch` | false | set to true to stop all new trades immediately |
| `risk.min_reward_risk` | 3 | only take setups with at least 3:1 potential |
| `scanner.min_score` | 50 | only take setups scoring 50+ out of 100 |
| `execution.max_contracts_per_order` | 100 | safety cap: any bigger order is refused |
| `paper.notify` | trades | trades / alerts / none: what gets posted to you |
| `reasoning.enabled` | true | Claude reviews each trade before it's placed |
| `reasoning.monthly_budget_usd` | 20 | Claude spending stops for the month at this amount |
| `reasoning.when_unavailable` | rules | no key / over budget: rules = trade anyway, skip = no trade |
| `news.max_pause_minutes` | 60 | longest pause the news desk may call for breaking news |
| `risk.shock_pause` | 6x / 15 min | sudden-move guard |
| `risk.drawdown_brake` | 10% / x0.5 | trade at half risk while 10%+ below the account's high |
| `execution.weekend.flatten` | true | close everything 30 minutes before Friday's close |
| `execution.stale_exit.hours` | 0 (off) | close trades that went nowhere after this many hours |
| `execution.score_sizing.enabled` | false | risk less on lower-scoring setups (never more than 1.5%) |
| `broker.name` | sim | sim = paper only; tradovate = also mirror trades at Tradovate |
| `broker.environment` | demo | demo = practice money; live = real money (needs mode: live) |
| `broker.dry_run` | true | write down orders instead of sending them |
| `sessions.entry_windows` | all | trade any time a good setup appears |
| `costs.commission_per_side` | 0.85 | per contract, all fees included |

## How a trade is managed

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

## How the agents decide (Phase 2)

- **Market Regime Agent** looks at hourly bars and labels each market: trending up, trending down, ranging or
  in transition, and whether volatility is low, normal or high. Each setup only runs in the conditions it suits
  (for example, buying dips only in an uptrend; fading extremes only in a quiet range).
- **Scanner** watches 15-minute bars for nine setups: trend breakout, pullback in a trend, mean reversion,
  liquidity sweep (a fake break of a high/low that snaps back), squeeze breakout, and four built on the levels
  futures traders watch: opening-range breakout (the first 30 minutes of the main session), a break of
  yesterday's high or low, a rejection of the overnight high or low, and a reclaim of the day's average price
  (VWAP) in a trend. The last four were added recently and haven't been tested on real history yet. Stops go beyond real market
  structure and targets at real levels. Each setup gets a 0-100 score from trend alignment, volume, reward-to-risk
  and session, and only the best one per market is passed on.
- **Risk Manager** has the final say on every trade, checked in this order: kill switch, circuit breaker,
  weekly limit, daily limit, losing streak, number of open trades, news blackout, and the MES/MNQ shared budget.
  It can approve, approve a smaller size, or veto, and writes the reason in plain English every time.

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
