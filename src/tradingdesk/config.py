"""Loads and validates the settings file.

All risk limits, sessions, contracts and the trading mode come from YAML (config/default.yaml by default).
Validation happens once, at startup: a typo or an unsafe value stops the program with a plain message
instead of surprising you in the middle of a trading day.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.yaml"

Mode = Literal["backtest", "paper", "live"]


class Account(BaseModel):
    starting_equity: float = Field(gt=0)


class CorrelatedGroup(BaseModel):
    name: str
    symbols: list[str]
    max_combined_risk_pct: float = Field(gt=0, le=10)


class ShockPause(BaseModel):
    """Sudden-move guard: a 1-minute bar this many times bigger than normal pauses new entries on that contract."""

    range_multiple: float = Field(6.0, ge=0)   # 0 = off
    minutes: int = Field(15, ge=0)


class Risk(BaseModel):
    risk_per_trade_pct: float = Field(gt=0, le=5)
    max_daily_loss_pct: float = Field(gt=0, le=20)
    max_consecutive_losses: int = Field(ge=1)
    max_weekly_drawdown_pct: float = Field(gt=0, le=30)
    max_total_drawdown_pct: float = Field(20.0, ge=0, le=100)
    max_open_positions: int = Field(ge=1)
    correlated_groups: list[CorrelatedGroup] = []
    min_reward_risk: float = Field(ge=1)
    news_blackout_minutes: int = Field(ge=0)
    calendar_file: Path | None = None
    weekly_resume: Literal["auto", "manual"] = "auto"
    kill_switch: bool = False
    shock_pause: ShockPause = ShockPause()

    @model_validator(mode="after")
    def _daily_at_least_one_trade(self) -> Risk:
        if self.max_daily_loss_pct < self.risk_per_trade_pct:
            raise ValueError("risk.max_daily_loss_pct must be at least risk.risk_per_trade_pct")
        return self


class Window(BaseModel):
    name: str
    start: time
    end: time


class Sessions(BaseModel):
    timezone: str = "America/New_York"
    entry_windows: Literal["all"] | list[Window] = "all"
    daily_break: Window | dict = {"start": "17:00", "end": "18:00"}

    @field_validator("daily_break", mode="before")
    @classmethod
    def _break(cls, v):
        if isinstance(v, dict) and "name" not in v:
            v = {"name": "daily_break", **v}
        return v


class Costs(BaseModel):
    commission_per_side: float = Field(ge=0)
    slippage_ticks: dict[str, float]

    def slippage_for(self, session: str) -> float:
        return self.slippage_ticks.get(session, max(self.slippage_ticks.values()))


class ScaleIn(BaseModel):
    min_contracts_for_thirds: int = Field(ge=2)
    starter_fraction: float = Field(gt=0, lt=1)
    confirm_timeframe: str = "1m"
    confirm_timeout_bars: int = Field(ge=1)


class Trail(BaseModel):
    method: Literal["atr", "swing"] = "atr"
    atr_period: int = Field(ge=2)
    atr_multiple: float = Field(gt=0)


class Exits(BaseModel):
    partial_at_r: float = Field(gt=0)
    partial_fraction: float = Field(gt=0, lt=1)
    breakeven_at_r: float = Field(gt=0)
    trail: Trail


class Stops(BaseModel):
    atr_buffer: float = Field(ge=0)


class MinLiquidity(BaseModel):
    min_volume_1m: int = Field(ge=0)


class Execution(BaseModel):
    scale_in: ScaleIn
    exits: Exits
    stops: Stops
    max_reentries: int = Field(ge=0)
    min_liquidity: MinLiquidity
    max_contracts_per_order: int = Field(100, ge=1)  # safety cap: the Execution Agent refuses anything bigger


class Regime(BaseModel):
    timeframe: str = "1h"
    adx_period: int = Field(14, ge=2)
    trend_adx: float = 25
    range_adx: float = 20
    ema_period: int = Field(50, ge=2)
    vol_lookback_bars: int = Field(240, ge=20)
    high_vol_pct: float = Field(0.8, gt=0, lt=1)
    low_vol_pct: float = Field(0.2, gt=0, lt=1)
    allowed: dict[str, list[str]] = {}
    blocked_in_high_volatility: list[str] = []


class Scanner(BaseModel):
    timeframe: str = "15m"
    min_score: float = Field(50, ge=0, le=100)
    setups: dict[str, dict[str, Any]] = {}

    def enabled(self) -> dict[str, dict[str, Any]]:
        return {k: {p: v for p, v in cfg.items() if p != "enabled"} for k, cfg in self.setups.items() if cfg.get("enabled", True)}


class Roll(BaseModel):
    method: Literal["volume"] = "volume"
    confirm_days: int = Field(ge=1)


class Data(BaseModel):
    provider: Literal["databento", "synthetic", "yahoo"] = "databento"
    dataset: str = "GLBX.MDP3"
    store_path: Path = Path("data")
    default_schema: Literal["ohlcv-1m", "trades"] = "ohlcv-1m"
    max_cost_usd_per_download: float = Field(ge=0)
    roll: Roll


class Logging(BaseModel):
    level: str = "INFO"
    path: Path = Path("logs")


class Paper(BaseModel):
    """Paper trading on live prices."""

    data_provider: Literal["yahoo", "synthetic"] = "yahoo"
    state_path: Path = Path("paper-state")
    warmup_days: int = Field(29, ge=1)   # history fetched on the first run so indicators are ready from the start
    notify: Literal["trades", "alerts", "none"] = "trades"


class Reasoning(BaseModel):
    """The head trader: Claude reviews each trade idea that passed the Scanner and the Risk Manager."""

    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    monthly_budget_usd: float = Field(20.0, ge=0)        # all Claude use together (reviews + news) stops at this
    max_reviews_per_day: int = Field(30, ge=0)
    when_unavailable: Literal["rules", "skip"] = "rules"  # no key / over budget / error: trade on the rules, or skip


class News(BaseModel):
    """News & Sentiment Agent."""

    calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    feeds: list[str] = []
    sentiment_every_minutes: int = Field(60, ge=5)
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    budget_share: float = Field(0.5, ge=0, le=1)    # news may use at most this share of the monthly budget
    max_pause_minutes: int = Field(60, ge=0)        # longest pause the News Agent may call for a breaking story


class Watchdog(BaseModel):
    """System health checks run after every paper-trading update."""

    max_data_delay_minutes: int = Field(40, ge=1)        # the free feed is ~10 min behind; worry well beyond that
    max_minutes_between_runs: int = Field(120, ge=1)     # scheduled runs can be late; warn if far apart while open
    gap_check_hours: int = Field(6, ge=1)                # look for holes in the data this far back


class Settings(BaseModel):
    mode: Mode = "paper"
    account: Account
    risk: Risk
    contracts: list[str]
    regime: Regime = Regime()
    scanner: Scanner = Scanner()
    sessions: Sessions
    costs: Costs
    execution: Execution
    data: Data
    logging: Logging = Logging()
    paper: Paper = Paper()
    reasoning: Reasoning = Reasoning()
    news: News = News()
    watchdog: Watchdog = Watchdog()

    @field_validator("contracts")
    @classmethod
    def _known_contracts(cls, v: list[str]) -> list[str]:
        from tradingdesk.contracts import CONTRACTS

        unknown = [s for s in v if s not in CONTRACTS]
        if unknown:
            raise ValueError(f"Unknown contracts {unknown}. Known: {sorted(CONTRACTS)}")
        return v

    @model_validator(mode="after")
    def _live_needs_credentials(self) -> Settings:
        # Going live must be a deliberate act: the flag in the file AND broker credentials in the environment.
        if self.mode == "live":
            missing = [k for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD") if not os.environ.get(k)]
            if missing:
                raise ValueError(f"mode is 'live' but these environment variables are missing: {missing}")
        return self


def load_settings(path: str | Path | None = None, **overrides) -> Settings:
    """Read the YAML file and validate it. `overrides` replaces top-level keys (handy in tests)."""
    with open(path or DEFAULT_CONFIG) as f:
        raw = yaml.safe_load(f)
    raw.update(overrides)
    return Settings.model_validate(raw)
