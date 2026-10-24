"""Head trader (Claude reviews), News & Sentiment Agent, calendar additions, sudden-move guard, Claude budget."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.conftest import minute_bars
from tests.test_phase3_paper import DelayedFeed, _tick, offline
from tests.test_trading_rules import FLAT, LONG, Scripted
from tradingdesk.agents.news import NewsAgent, NewsLog, parse_feed, parse_ff_calendar
from tradingdesk.agents.reasoning import Overseer
from tradingdesk.agents.risk import RiskManager
from tradingdesk.backtest.engine import Backtester
from tradingdesk.calendar import EconomicCalendar, build_default_calendar
from tradingdesk.live.paper import PaperTrader
from tradingdesk.llm import Claude, SpendLedger, cost_of


# ----- a stand-in for the Anthropic client ------------------------------------------------------------------
class FakeClient:
    def __init__(self, answers, stop_reason="end_turn"):
        self.answers = list(answers)
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        ans = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(model=kw["model"], stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0),
                               content=[SimpleNamespace(type="text", text=json.dumps(ans))])


def claude(answers, budget=20.0, path=None, **kw):
    return Claude(budget, SpendLedger(path), client=FakeClient(answers, **kw))


# ----- Claude wrapper ------------------------------------------------------------------------------------
def test_cost_and_budget(tmp_path):
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    assert cost_of("claude-opus-5-5", u) == pytest.approx(4.0 + 2.0)
    assert cost_of("some-new-model", u) == pytest.approx(10.0 + 5.0)   # unknown: priced high, to be safe
    c = claude([{"x": 1}], budget=0.02, path=tmp_path / "spend.json")
    first = c.ask("reviews", "claude-opus-5-5", "medium", "sys", "hi", {"type": "object"})
    assert first.data == {"x": 1} and first.cost == pytest.approx(0.018)
    kw = c.client().calls[0]
    assert kw["fallbacks"] == "default" and kw["output_config"]["format"]["type"] == "json_schema"
    c.ask("reviews", "claude-opus-5-5", "medium", "sys", "hi", {"type": "object"})
    third = c.ask("reviews", "claude-opus-5-5", "medium", "sys", "hi", {"type": "object"})
    assert third.data is None and "budget" in third.error and len(c.client().calls) == 2
    assert SpendLedger(tmp_path / "spend.json").spent("reviews") == pytest.approx(0.036)


def test_refusal_is_an_error_not_an_answer():
    a = claude([{"x": 1}], stop_reason="refusal").ask("news", "claude-opus-5-5", "low", "s", "u", {})
    assert a.data is None and "declined" in a.error


def test_no_key_means_off(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = Claude(20, SpendLedger(None))
    assert not c.available and "ANTHROPIC_API_KEY" in c.ask("x", "m", "low", "s", "u", {}).error


# ----- Head trader in the engine -------------------------------------------------------------------------
ROWS = [FLAT] * 5 + [(5000, 5001, 5000, 5001)] * 30


def _run(settings, reviewer):
    return Backtester(settings, [Scripted(LONG)], {"MES": minute_bars(ROWS)}, reviewer=reviewer).run()


def test_head_trader_can_veto(settings):
    ov = Overseer(settings, claude([{"decision": "reject", "size_factor": 0, "reason": "Straight into resistance."}]))
    res = _run(settings, ov)
    assert res.trades == []
    d = next(d for d in res.decisions if d.agent == "overseer")
    assert d.action == "rejected" and "resistance" in d.reason


def test_head_trader_can_only_make_trades_smaller(settings):
    full = _run(settings, None).trades[0].max_contracts
    ov = Overseer(settings, claude([{"decision": "reduce", "size_factor": 3.0, "reason": "News risk."}]))
    res = _run(settings, ov)
    assert next(d for d in res.decisions if d.agent == "overseer").details["factor"] == 0.5  # 3.0 is not allowed
    assert res.trades[0].max_contracts < full
    ov2 = Overseer(settings, claude([{"decision": "approve", "size_factor": 9, "reason": "Clean."}]))
    assert _run(settings, ov2).trades[0].max_contracts == full


def test_reviews_are_remembered_and_unavailable_follows_settings(settings, tmp_path):
    c = claude([{"decision": "approve", "size_factor": 1, "reason": "ok"}])
    first = Overseer(settings, c, tmp_path / "ov.json")
    _run(settings, first)
    first.save()
    _run(settings, Overseer(settings, c, tmp_path / "ov.json"))   # same idea again: answered from memory
    assert len(c.client().calls) == 1
    settings.reasoning.when_unavailable = "skip"
    assert _run(settings, Overseer(settings, None)).trades == []
    settings.reasoning.when_unavailable = "rules"
    assert _run(settings, Overseer(settings, None)).trades


def test_brief_shows_the_chart_account_and_news(settings):
    log = NewsLog(snapshots=[{"ts": "2024-03-05T14:00:00+00:00", "risk_level": "elevated", "pause_minutes": 0, "affected": [],
                              "summary": "Stocks slip on yields.", "markets": {"MES": {"mood": "leaning bearish", "note": "yields up"}}}],
                  headlines=[{"seen": "2024-03-05T14:10:00+00:00", "published": "2024-03-05T14:05:00+00:00", "source": "CNBC", "title": "Yields jump"}])
    c = claude([{"decision": "approve", "size_factor": 1, "reason": "ok"}])
    _run(settings, Overseer(settings, c, news=log, calendar=EconomicCalendar(build_default_calendar(2024, 2024))))
    text = c.client().calls[0]["messages"][0]["content"]
    for part in ("## Trade idea", "LONG (buy) MES", "## Account", "Equity $50,000", "Yields jump", "leaning bearish", "Scheduled releases"):
        assert part in text, part


# ----- News & Sentiment ----------------------------------------------------------------------------------
FF = json.dumps([
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-28T08:30:00-04:00", "impact": "High"},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-10-28T10:30:00-04:00", "impact": "Medium"},
    {"title": "German Ifo", "country": "EUR", "date": "2026-10-28T04:00:00-04:00", "impact": "High"},
    {"title": "Bank Holiday", "country": "USD", "date": "2026-10-29T00:00:00-04:00", "impact": "High"},
])
RSS = b"""<rss><channel>
<item><title>Fed holds rates steady</title><pubDate>Wed, 28 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title>Oil jumps on supply fears</title><pubDate>Wed, 28 Oct 2026 18:20:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Gold at record</title><updated>2026-10-28T18:30:00Z</updated></entry></feed>"""


def test_calendar_parsing_and_per_contract_blackouts():
    cal = parse_ff_calendar(FF)
    assert list(cal["event"]) == ["CPI m/m", "Crude Oil Inventories"]
    assert cal.iloc[1]["symbols"] == "MCL"
    ec = EconomicCalendar(cal)
    at = pd.Timestamp("2026-10-28 14:35", tz="UTC")    # 10:35 ET
    assert ec.blocking_event(at, 10, "MCL")[0] == "Crude Oil Inventories"
    assert ec.blocking_event(at, 10, "MES") is None
    base = build_default_calendar(2024, 2024)
    assert "2024-03-12 08:30" in base[base["event"].str.contains("CPI")]["time_et"].tolist()


def test_feeds_parse_rss_and_atom():
    assert [h["title"] for h in parse_feed(RSS, "X")] == ["Fed holds rates steady", "Oil jumps on supply fears"]
    assert parse_feed(ATOM, "Y")[0]["published"].startswith("2026-10-28T18:30")


def test_news_agent_reads_mood_and_can_pause_trading(settings, tmp_path):
    settings.news.feeds = ["https://www.cnbc.com/rss", "https://example.com/atom"]
    pages = {settings.news.calendar_url: FF.encode(), "https://www.cnbc.com/rss": RSS, "https://example.com/atom": ATOM}
    answer = {"markets": [{"symbol": "MCL", "mood": "bullish", "note": "supply scare"}], "risk_level": "extreme",
              "pause_minutes": 500, "affected": ["MCL"], "summary": "Major pipeline attack."}
    na = NewsAgent(settings, tmp_path, claude([answer]), http=lambda url: pages[url])
    now = pd.Timestamp("2026-10-28 18:40", tz="UTC")
    na.update(now)
    snap = na.log.snapshot_at(now)
    assert snap["pause_minutes"] == settings.news.max_pause_minutes          # capped
    assert na.log.pause_at("MCL", now + pd.Timedelta(minutes=30)) and not na.log.pause_at("MES", now + pd.Timedelta(minutes=30))
    assert na.log.pause_at("MCL", now - pd.Timedelta(minutes=1)) is None      # never applied before it was known
    assert na.log.snapshot_at(now - pd.Timedelta(minutes=1)) is None
    na.update(now + pd.Timedelta(minutes=10))                                 # too soon to ask Claude again
    assert len(na.claude.client().calls) == 1
    # The Risk Manager honours the pause.
    from tests.test_phase2_agents import FakeEngine, _b
    from tradingdesk.backtest.models import TradePlan
    rm = RiskManager(settings, EconomicCalendar(build_default_calendar(2026, 2026)), news=na.log)
    plan = TradePlan("MCL", 1, 70, 69.5, 72, "t", "t")
    v = rm.check(plan, FakeEngine(), _b("2026-10-28 19:00"))
    assert not v.allowed and "News pause" in v.reason


def test_past_calendar_entries_never_change(settings, tmp_path):
    pages = {settings.news.calendar_url: FF.encode()}
    na = NewsAgent(settings, tmp_path, None, http=lambda url: pages[url])
    na.update_calendar(pd.Timestamp("2026-10-26", tz="UTC"))
    pages[settings.news.calendar_url] = json.dumps([]).encode()   # next week's file no longer lists them
    na.update_calendar(pd.Timestamp("2026-10-29", tz="UTC"))
    assert "CPI m/m" in na.calendar_frame()["event"].tolist()


# ----- Sudden-move guard ---------------------------------------------------------------------------------
def test_sudden_move_pauses_new_entries(settings):
    calm = [(5000, 5000.5, 4999.5, 5000)] * 80
    shock = [(5000, 5030, 4990, 5025)]
    rows = calm + shock + [(5025, 5026, 5024, 5025)] * 5 + [(5025, 5026, 5025, 5026)] * 20
    plan_at = pd.Timestamp("2024-03-05 14:30", tz="UTC") + pd.Timedelta(minutes=84)

    class Late(Scripted):
        def on_bar(self, symbol, bars, i, ctx):
            if pd.Timestamp(bars.index[i]) + pd.Timedelta(minutes=5) >= plan_at and not getattr(self, "done", False):
                self.done = True
                from tradingdesk.backtest.models import TradePlan
                return [TradePlan(symbol, 1, 5026, 5016, 5080, "test", "test", "5m")]
            return []

    res = Backtester(settings, [Late(LONG)], {"MES": minute_bars(rows)}, risk_gate=RiskManager(settings)).run()
    assert any(d.action == "shock" for d in res.decisions)
    assert any("Sudden move" in d.reason for d in res.decisions if d.action == "rejected")


# ----- Paper trading with the head trader ---------------------------------------------------------------
def test_paper_asks_once_per_idea_and_replays_identically(settings, tmp_path):
    settings.mode = "paper"
    settings.contracts = ["MES", "MGC"]
    settings.paper.state_path = tmp_path / "paper"
    settings.paper.warmup_days = 20
    feed = DelayedFeed(settings.contracts, "2024-02-01", "2024-04-01")
    c = claude([{"decision": "reduce", "size_factor": 0.5, "reason": "Take it smaller."}])
    pt = PaperTrader(settings, feed, claude=c, http=offline)
    _tick(pt, feed, "2024-03-04 15:00")
    for t in ("2024-03-06 15:00", "2024-03-07 15:00", "2024-03-08 15:00"):
        r = _tick(pt, feed, t)
        assert next(x for x in r.checks if x.name == "history").status == "ok"
    reviews = json.loads((settings.paper.state_path / "overseer.json").read_text())
    assert reviews and len(c.client().calls) == len(reviews)
    journal = (settings.paper.state_path / "journal.jsonl").read_text()
    assert '"agent": "overseer"' in journal
    assert "Claude spending this month" in r.status_md
