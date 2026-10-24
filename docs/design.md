# Design decisions (Phase 1)

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

## What Phase 1 deliberately leaves out
Portfolio risk limits (daily/weekly loss, losing streaks, correlated exposure, news blackouts) belong to the Risk
Manager in Phase 2 and plug into the engine's `RiskGate` hook. Real strategies belong to the Scanner and Regime
agents in Phase 2.

## Things to verify on the first real data
- The Databento request shape (`MES.FUT`, `stype_in=parent`) is covered by mocked tests only; this environment
  cannot reach Databento. The first "Download market data" run confirms it.
- Commission per side (0.85) is an estimate; set it to your Tradovate plan's all-in rate.
