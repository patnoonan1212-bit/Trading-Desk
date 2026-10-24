"""A whole backtest on made-up data for several contracts: the pieces must fit together and the books balance."""

import pytest

from tradingdesk.backtest.engine import Backtester
from tradingdesk.backtest.metrics import summarize, trades_frame
from tradingdesk.backtest.report import write_report
from tradingdesk.data.agent import DataAgent
from tradingdesk.data.providers.synthetic import SyntheticProvider
from tradingdesk.data.store import DataStore
from tradingdesk.strategies.sanity_breakout import SanityBreakout


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    from tradingdesk.config import load_settings

    s = load_settings(mode="backtest")
    agent = DataAgent(s, SyntheticProvider(), DataStore(tmp_path_factory.mktemp("data")))
    data = {}
    for sym in ("MES", "MCL"):
        agent.download(sym, "2024-02-01", "2024-04-01")
        data[sym] = agent.continuous(sym)
    return Backtester(s, [SanityBreakout()], data).run(), tmp_path_factory.mktemp("report")


def test_trades_happen_on_both_contracts(result):
    res, _ = result
    assert {t.symbol for t in res.trades} == {"MES", "MCL"}


def test_books_balance(result):
    res, _ = result
    assert res.equity.iloc[-1] == pytest.approx(res.settings.account.starting_equity + sum(t.net_pnl for t in res.trades), abs=0.05)


def test_no_trade_loses_much_more_than_its_risk(result):
    res, _ = result
    # Gaps through a stop can cost a little extra; anything far beyond the budget would be a sizing bug.
    for t in res.trades:
        assert -t.net_pnl <= t.risk_dollars * 1.25, (t.id, t.net_pnl, t.risk_dollars)


def test_every_trade_has_a_stop_from_the_first_contract(result):
    res, _ = result
    for t in res.trades:
        assert t.initial_stop and t.fills[0].purpose == "entry"


def test_report_is_written(result):
    res, out = result
    rep = write_report(res, out)
    assert (out / "report.html").exists() and (out / "trades.csv").exists()
    assert summarize(trades_frame(res.trades))["trades"] == len(res.trades)
    assert "In plain English" in rep["markdown"]
