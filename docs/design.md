# Design decisions

Short explanations of the main choices, so later phases (and anyone reviewing) know why things are the way they are.

## Agents talk through an event bus
Agents publish typed events (`core/events.py`) instead of calling each other. Any agent can crash or be replaced
without touching the others, and there is one place to log every decision. Phase 1 uses a simple in-process bus;
later phases can swap in a networked queue behind the same `publish`/`subscribe` interface.

## One code path for backtest, paper and live
Strategies only see completed bars and only emit trade plans; the engine, simulated broker and trade manager turn
plans into orders. In Phase 3 the simulated broker is replaced by the paper/live broker; strategies don't change.
That is the best protection against "worked in the backtest, fails live".

## Data: Databento for history, Parquet + DuckDB for storage
Tradovate's API has limited history, so history comes from Databento (CME Globex MDP3), behind a provider
interface that Tradovate will also implement for live data. Every download is priced first and refused above a
configurable limit. Data is stored as Parquet files queried with DuckDB: compact, fast, no database server.

## Building continuous contracts ourselves
All contract months are downloaded. The roll happens when a later contract out-trades the current one, decided
after the session ends and applied from the next session (no look-ahead). Two price series are kept: actual
contract prices and additively back-adjusted prices. Within a contract they move identically, so the engine works
on back-adjusted prices (no fake jumps at rolls) and records actual prices on every fill. Holding through a roll
is charged a round trip of commission and slippage.

## Conservative fill simulation
Orders fill from the next 1-minute bar; stops fill at the stop or worse, including gaps; limits need price to trade
through; slippage is set per session (larger overnight); fills are capped at a share of the minute's volume; if the
stop and target are both reached in one minute the stop is assumed first. Results should be slightly pessimistic,
which is the safe direction.

## Risk budget shared across scale-in entries
The 1.5% risk limit covers all entries of a trade. The add-on is sized at confirmation time from the budget that's
left (adding after price has moved means a wider stop distance, so the add is smaller). If confirmation pushes even
a single-contract trade over budget, the trade is skipped. Sizing adapts to account size: below 3 contracts the
trade enters once after confirmation and skips the partial exit.

## Things to verify on the first real data
- The Databento request shape (`MES.FUT`, `stype_in=parent`) is covered by mocked tests only; this environment
  cannot reach Databento. The first "Download market data" run confirms it.
- Commission per side (0.85) is an estimate; set it to your Tradovate plan's all-in rate.

# Phase 2

## Regime first, then setups
Most setups only work in certain conditions: breakouts in trends, mean reversion in ranges. The Regime Agent
classifies each market on hourly bars (ADX and a 50-period EMA for trend; where today's ATR sits in its last
240 hours for volatility). A reading is only used after its hour has finished, so a backtest never peeks at the
future. Which setups may run in which regime is a table in the settings, not code.

## Five setups, one transparent score
The Scanner uses a handful of well-known setups rather than one clever one, so no single pattern carries the
account. Stops and targets come from market structure (recent highs/lows, the far side of a range), not fixed
distances, and anything under 3:1 is dropped. The score adds up visible parts (trend alignment, volume,
reward-to-risk, session) so every trade's reason can be read back.

## The Risk Manager can shrink, not just veto
`RiskGate.check` returns a verdict with an optional smaller budget. That matters for MES and MNQ, which move
together: if one is already open, a second index trade may still be allowed at a smaller size that keeps the
pair inside 2% combined risk. If less than a quarter of a normal trade's budget is left, it's rejected.

## Circuit breaker
A synthetic stress run showed that daily and weekly limits alone still allowed a long slide over many weeks.
The circuit breaker stops new trades once the account is 20% below its peak and does not restart on its own; you
reset it. It can be turned off (set to 0) for research runs, and the walk-forward test does so in order to
measure the raw strategy.

## News blackout from a calendar file
Jobs reports (using the Labor Department's real scheduling rule, not "first Friday") and Fed decisions for
2022-2026 are in `config/economic_calendar.csv`. CPI and other releases arrive with the News Agent in Phase 4.

## Walk-forward instead of one big optimisation
Settings are tuned on 12 months and judged on the following 3, repeatedly. Choices are ranked by a t-statistic of
R per trade with a penalty for complexity, so a setting has to be consistently good, not lucky once. A setup is
switched off in the next window if it had under 20 training trades or a t-statistic under 1.

## Free delayed data for Phase 3 paper trading
Real-time CME data costs money. Paper trading first runs on free delayed prices to prove the live plumbing
(orders, positions, Watchdog) end to end. Before any live decision it switches to Tradovate's real-time feed,
because delayed data can't judge fills accurately.

# Phase 3

## Free delayed prices from Yahoo, contract by contract
Paper trading needs fresh prices but doesn't need them instantly, so it uses Yahoo Finance's free CME prices
(about 10 minutes behind, last ~30 days of 1-minute bars). We ask for individual contract months (`MESZ26.CME`)
rather than Yahoo's rolling `MES=F`, so contract rolls are handled by the same volume rule as the Databento
history; the rolling series is only a fallback, flagged by the Watchdog. Yahoo turns away plain scripted requests
from cloud servers, so requests go through `curl_cffi`, which looks like a normal browser. Real-time data via
Tradovate replaces this before any live decision, because delayed prices can't judge fills precisely.

## The Execution Agent sits between trades and the broker
Orders from every trade go through `ExecutionAgent` to a `Broker`. In backtest and paper mode the broker is the
simulated one with the cautious fill rules from Phase 1, so paper results are directly comparable with backtests.
Before an order goes out it is checked (positive size, at most `max_contracts_per_order`, a valid price for
stop and limit orders). Live mode can't create a broker until the Tradovate connection exists (Phase 6), on top
of the existing rule that live mode needs the flag in the settings file and broker credentials.

## Paper trading by replay
Each update fetches new prices, then replays the whole desk over every recorded price since paper trading began.
The recorded prices are the only state. That makes updates safe to run late, twice, or after a crash, and it
guarantees that paper trading follows the backtest rules exactly. The cost is a few seconds per update, which
grows slowly over months.

Replay needs care in three places:
- **Incomplete bars.** A 15-minute bar whose time isn't over when the data stops is not used yet, otherwise the
  Scanner would see a partial bar and decide differently on the next update.
- **Rolls.** After a roll, earlier prices are back-adjusted, so the numbers in older decision texts change even
  though the decisions don't. Decisions are matched on what they were (time, agent, action, contract, trade),
  and the journal keeps the original wording in real contract prices.
- **Settings changes.** Replaying with new settings can change past decisions. The kill switch is applied only
  from the newest price seen before it was turned on, so it never rewrites history. Other changes are reported
  by the Watchdog as "history changed"; the journal keeps what was originally announced.

Real-money trading can't replay real orders. In Phase 6 the broker's positions and orders are the truth, and the
Execution Agent reconciles against them.

## Running on GitHub, telling you through an issue
With no server yet, a scheduled GitHub Actions workflow runs an update every 15 minutes while the market is open.
The paper account is kept in GitHub's Actions cache between runs (not publicly downloadable). The status page is
the body of a "Paper trading desk" issue; trades and alerts are comments, so GitHub's own email and mobile
notifications reach you without Telegram or another app. Phase 6 moves this to a server with proper alerts.

## Market data in a public repository
Databento data is licensed for your own use, and release files in a public repository can be downloaded by
anyone, so the stored data file is encrypted with your Databento key (AES-256). If the key changes, download the
data again.
