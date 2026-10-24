import pytest
from pydantic import ValidationError

from tradingdesk.config import load_settings
from tradingdesk.contracts import root_of, spec
from tradingdesk.data.symbols import is_outright, parse, sort_key


def test_defaults_are_safe():
    s = load_settings()
    assert s.mode == "paper"
    assert s.account.starting_equity == 50000
    assert s.risk.risk_per_trade_pct == 1.5


def test_live_mode_needs_credentials(monkeypatch):
    monkeypatch.delenv("TRADOVATE_USERNAME", raising=False)
    monkeypatch.delenv("TRADOVATE_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="live"):
        load_settings(mode="live")


def test_bad_limits_rejected():
    with pytest.raises(ValidationError):
        load_settings(contracts=["MES", "ZZZ"])
    s = load_settings()
    raw = s.model_dump()
    raw["risk"]["max_daily_loss_pct"] = 1.0  # smaller than the per-trade risk
    with pytest.raises(ValidationError):
        type(s).model_validate(raw)


def test_contract_math():
    assert spec("MES").tick_value == 1.25
    assert spec("MNQ").tick_value == 0.5
    assert spec("MCL").tick_value == pytest.approx(1.0)
    assert spec("MGC").tick_value == pytest.approx(1.0)
    assert spec("MESZ5").symbol == "MES"
    assert root_of("MCLF26") == "MCL"
    assert spec("MES").round_to_tick(5000.13) == 5000.25  # nearest tick
    assert spec("MES").round_to_tick(5000.01, "up") == 5000.25
    assert spec("MCL").round_to_tick(70.004, "down") == 70.0


def test_symbols():
    from datetime import date

    assert parse("MESZ5", date(2025, 6, 1)) == ("MES", 2025, 12)
    assert parse("MESH6", date(2025, 11, 1)) == ("MES", 2026, 3)
    assert parse("MESZ9", date(2020, 1, 1)) == ("MES", 2019, 12)
    assert parse("MCLF26") == ("MCL", 2026, 1)
    assert not is_outright("MESZ5-MESH6")
    assert sort_key("MESH6", date(2025, 12, 1)) > sort_key("MESZ5", date(2025, 12, 1))
