"""Learning Agent, settings editing, and the dashboard."""

import json
import shutil

import numpy as np
import pandas as pd

from tradingdesk import dashboard
from tradingdesk.agents.learning import LearningAgent, check_proposals, groups, propose, report_markdown, verdict, write
from tradingdesk.agents.watchdog import Check
from tradingdesk.config import DEFAULT_CONFIG, load_settings
from tradingdesk.config_edit import Change, apply_to_file, with_changes


def _trades(n, setup="pullback", r=0.5, spread=0.3, **cols):
    rng = np.random.default_rng(len(setup) + n)
    rs = r + rng.normal(0, spread, n)
    t0 = pd.Timestamp("2024-03-04 15:00", tz="UTC")
    times = [t0 + pd.Timedelta(hours=5 * i) for i in range(n)]
    base = {"setup": setup, "trend": "trending_up", "volatility": "normal", "session": "us_regular", "direction": "long",
            "score": 70.0, "exit": "trailing/breakeven stop hit", "stopped_then_target": None, "mfe_r": 1.5,
            "planned_at": times, "opened_at": times, "closed_at": times, "symbol": "MES"}
    base.update(cols)
    df = pd.DataFrame({**base, "r": rs})
    df["net_pnl"] = df["r"] * 750
    return df


def test_verdicts_need_enough_trades():
    assert verdict(10, 5.0, 30) == "too early"
    assert verdict(40, 2.5, 30) == "clearly positive" and verdict(40, -1.2, 30) == "leaning negative"


def test_proposals_follow_the_evidence(settings):
    from tradingdesk.agents.learning import enrich

    df = enrich(pd.concat([
        _trades(60, "pullback", 0.4),
        _trades(60, "mean_reversion", -0.4, trend="ranging"),
        _trades(40, "liquidity_sweep", -0.5, trend="transition"),
        _trades(40, "liquidity_sweep", 0.6, trend="ranging"),
    ], ignore_index=True))
    props = {p.title: p for p in propose(df, settings, 30)}
    assert "Switch off mean reversion" in props
    assert props["Switch off mean reversion"].changes[0].path == "scanner.setups.mean_reversion.enabled"
    sweep = props["Stop liquidity sweep when the market is transition"].changes[0]
    assert "transition" in sweep.old and "transition" not in sweep.new
    assert not any("pullback" in t for t in props)                  # a winner is left alone
    assert not any("risk" in c.path for p in props.values() for c in p.changes)  # risk limits are never touched
    assert all(g.verdict == "too early" for g in groups(df.head(10), 30))


def test_low_scores_and_tight_stops(settings):
    from tradingdesk.agents.learning import enrich

    low = _trades(50, "pullback", -0.5, score=55.0, exit="stopped out", stopped_then_target=True)
    high = _trades(50, "trend_breakout", 0.5, score=80.0)
    titles = [p.title for p in propose(enrich(pd.concat([low, high], ignore_index=True)), settings, 30)]
    assert "Only take setups scoring 60+" in titles or "Only take setups scoring 65+" in titles
    assert "Give stops a little more room" in titles


def test_settings_file_edit_keeps_comments_and_only_changes_what_it_should(tmp_path):
    path = tmp_path / "cfg.yaml"
    shutil.copy(DEFAULT_CONFIG, path)
    before = path.read_text().splitlines()
    done = apply_to_file(path, [Change("scanner.min_score", 50, 65), Change("scanner.setups.mean_reversion.enabled", True, False),
                                Change("regime.blocked_in_high_volatility", ["mean_reversion", "pullback"], ["mean_reversion", "pullback", "trend_breakout"])])
    after = path.read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert len(changed) == 3 and len(done) == 3
    assert "min_score: 65" in changed[1][1] and "# 0-100" in changed[1][1]
    s = load_settings(path)
    assert s.scanner.min_score == 65 and "mean_reversion" not in s.scanner.enabled()
    # A setting you changed yourself since the suggestion was made is left alone.
    assert "skipped" in apply_to_file(path, [Change("scanner.min_score", 50, 70)])[0]


def test_suggestions_are_tested_on_held_back_history(settings):
    from tradingdesk.agents.learning import Proposal
    from tradingdesk.data.providers.synthetic import SyntheticProvider
    from tradingdesk.data.rollover import build_continuous

    data = {"MES": build_continuous(SyntheticProvider().fetch_bars("MES", "2024-01-01", "2024-03-15"))}
    keep = Proposal("P1", "no-op", "", [Change("scanner.setups.squeeze_breakout.volume_mult", 1.5, 1.5)], "")
    check_proposals([keep], settings, data, pd.Timestamp("2024-02-01", tz="UTC"))
    assert keep.status == "failed" and "held-back history" in keep.test   # changing nothing can't beat the current settings
    assert with_changes(settings, [Change("scanner.min_score", 50.0, 70)]).scanner.min_score == 70


def test_review_writes_report_and_json(settings, tmp_path):
    paper = tmp_path / "paper"
    paper.mkdir()
    _trades(12, "pullback", 0.3).to_csv(paper / "trades.csv", index=False)
    rep = LearningAgent(settings, 30).review(None, paper)
    write(rep, tmp_path / "learning")
    md = (tmp_path / "learning" / "report.md").read_text()
    assert "12 closed paper trades" in md and "None this week" in md
    assert json.loads((tmp_path / "learning" / "latest.json").read_text())["trades"] == 12
    assert "Nothing to learn" in report_markdown(LearningAgent(settings).review(None, None))


def test_dashboard_page_is_self_contained_and_safe():
    curve = pd.Series([50000.0, 50500.0, 50200.0], index=pd.date_range("2024-03-04", periods=3, freq="1D", tz="UTC"))
    data = dashboard.build_data(now=pd.Timestamp("2024-03-06", tz="UTC"), started=curve.index[0], equity=50200, start_equity=50000,
                                day_start_equity=50500, open_risk=0, positions=[], equity_curve=curve, closed=pd.DataFrame(),
                                reviews={}, checks=[Check("x", "ok", "fine </script><script>alert(1)</script>")], news_snapshot=None,
                                upcoming=[], spend={"spent": 0.0, "reviews": 0.0, "news": 0.0, "budget": 20, "connected": False},
                                learning=None, health="ok")
    page = dashboard.render(data)
    assert page.count("</script>") == 1          # data can't close the script block early
    assert "src=" not in page                      # no outside scripts or images
    assert '"equity": 50200' in page
