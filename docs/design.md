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

# Phase 4

## Claude reviews, the rules decide
The head trader only sees ideas that already passed every rule, and it can only approve, shrink (25-75%) or
reject. The Risk Manager's limits stay the final word and nothing Claude says can loosen them. This keeps Claude
where judgment adds something (context the rules can't encode) without letting a model error create risk. It
also keeps the cost down: Claude is never asked about trades the rules would refuse anyway.

## Structured answers, a hard budget, fail-safe defaults
Every Claude call asks for JSON that must match a schema, so answers can't be misread. Each call's cost is
worked out from the tokens it used and recorded; at `monthly_budget_usd` calls stop until next month, with news
allowed at most half. No key, budget used up, an API error, or a declined request all fall back to
`when_unavailable` (rules by default). Requests opt into Anthropic's automatic fallback model if a safety check
declines them. The model is Claude Opus 5.5 by default; `reasoning.model` and `news.model` can be set to a cheaper
model.

## Replay stays exact
Paper trading replays history every update, but Claude must be asked once per idea and give the same answer on
every replay. Reviews are stored with the idea (time, contract, setup, direction) in `overseer.json` and reused.
News works the same way: every headline and every mood reading is stored with the time the desk learned it, and
the desk only ever uses what it knew at the moment of a decision. Calendar events in the past are never changed.

## No Claude in backtests
Claude was trained on market history, so asking it about a 2023 trade would leak hindsight into the results.
Backtests and walk-forward tests therefore run on the rules alone. The head trader's value is measured in paper
trading.

## Two kinds of news protection
Scheduled releases come from a calendar (built-in dates for jobs, inflation and Fed decisions back to 2022, plus
this week's live calendar). Unscheduled news is caught two ways: Claude reading headlines (paper and live), and the
sudden-move guard, a plain price rule that also runs in backtests so its effect can be measured.

# Phase 5

## Learn on one stretch, test on another
With dozens of ways to slice the trades, some slices always look great or terrible by chance. So the Learning
Agent finds ideas on one set of trades (paper trades plus the first two thirds of history) and tests each idea
by re-running the backtest on the last third, which it never looked at. An idea must add total R there without
making the worst drop more than 1.5 points worse. Groups need 30+ trades before they count, and verdicts use a
t-statistic, so "won 4 of 5" never becomes a rule.

## Suggestions are settings, and you approve them
Each suggestion is a short list of setting changes, applied to `config/default.yaml` with ruamel.yaml so comments
and layout survive and the pull request shows exactly the lines that change. Risk limits are off-limits to the
agent. A suggestion is skipped if you have changed that setting yourself since it was made. Merging is the only way
a suggestion takes effect, which also leaves a clear history of what changed and why.

## Judging the head trader fairly
Claude can't be judged on history (it knows how history turned out), and its own paper results can't be judged
in isolation. The Learning Agent replays the paper period with the same prices, news and risk rules but without
Claude, and compares the accounts. For every idea Claude passed on or shrank, it shows what the rules alone would
have made.

## Dashboard: one static page
The dashboard is a single HTML page with the data embedded and the charts drawn in plain JavaScript: no server, no
database, no outside scripts, nothing to break or keep patched. It follows a tested data-visualisation method:
one axis per chart, a blue/red pair only for gains and losses, status colours always paired with an icon and a
word, a table view behind every chart, and separate colour choices for light and dark mode. GitHub Pages hosts it;
it is published at most about hourly (more often when there's news) because Pages limits how often a site can update.

# Phase 6

## The broker mirrors a twin instead of being driven trade by trade
The desk keeps making every decision on its simulated twin, exactly as in paper trading. After each update the
Execution Agent compares the Tradovate account with the twin and fixes differences: market orders for position
size, one real stop order per position at the twin's stop. This "make it match" design recovers by itself from
missed updates, restarts and partial failures, because each update starts from what the broker actually holds.
Exits and stop moves are always copied; a new entry is only copied while it is fresh (time and price limits),
otherwise that trade stays paper-only and you are told.

## Protection lives at the broker
Stops are real orders at Tradovate, sized to what the broker actually holds after market orders fill, so a
stop can never be larger than the position. If the desk stops running, positions remain protected.

## A safety net that doesn't trust the twin
The real account's value is read every update. If it falls more than the daily loss limit, regardless of what
the twin thinks, everything is closed and the mirror stands aside until the next trading day.

## Dry run first, demo second, live last
Dry run keeps an imaginary broker account so it reports only changes, needs no account, and shows exactly what
would be sent. Live requires `mode: live`, `broker.environment: live`, Tradovate real-time prices, the Tradovate
secrets (all checked when settings load) and every item on the readiness checklist (checked every update). A
live configuration that fails the checklist runs in dry run and raises an alert.

## Email for alerts
Email (through a Gmail app password) was chosen over chat apps. Alerts fall back to the status issue if email isn't
set up or a send fails, and a failed send is itself a Watchdog alert.

## Untested against the real Tradovate yet
The Tradovate client follows Tradovate's published REST and WebSocket API and is tested against a simulated
Tradovate. It has not run against a real account. The read-only "Check Tradovate" button is the first step once a
demo account exists, and the checklist requires two clean weeks on demo before live is possible.

# After Phase 6: a better team

## Protection first, on by default
The drawdown brake (half risk while 10% below the high) and the Friday close are on by default. Both only ever
reduce risk: the brake makes a losing stretch shallower and shorter to recover from, and closing before the weekend
removes the one risk a stop can't handle, a gap past it. The stale-trade exit changes which trades win, not just
how much is at risk, so it stays off until the walk-forward has tested it.

## Sizing by score: less on weaker setups, never more than the cap
Higher conviction is expressed by risking *less* on weaker setups, never more than the 1.5% rule on the best ones.
It starts off. The Learning Agent proposes switching it on only when setups scoring 80+ have clearly beaten those
under 65 on enough trades, and the proposal must still pass the held-back history test. Claude's own confidence
never changes size: a language model's sense of certainty isn't a calibrated probability.

## The head trader gets the desk's memory
Each review now includes what the weekly learning review found about this setup and regime, and how the setup did
in the desk's own recent trades. Only findings that bear on the idea are included, so the prompt stays short.

## Four level-based setups
Opening range, previous-day high/low, overnight high/low and VWAP are where many futures traders place orders,
which is what can make these levels matter. Each level is computed only from finished bars and becomes available
only once it's known (the opening range from 30 minutes after the open, the overnight range from the open). Main
session opening times differ by contract (stock indexes 9:30, crude 9:00, gold 8:20 New York time). They are on in
paper trading so they gather evidence; the walk-forward test will judge them once real history is downloaded.
