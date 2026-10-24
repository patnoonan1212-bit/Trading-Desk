"""Typed messages that agents send each other.

Agents never call each other directly; they publish these events on the bus. That keeps each agent
independent (one can crash or be replaced without touching the others) and gives one place to log
every decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Severity = Literal["info", "warning", "critical"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    created_at: datetime = field(default_factory=_now, kw_only=True)

    @property
    def kind(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind
        return d


@dataclass(frozen=True)
class DataQualityIssue(Event):
    """Raised by the Data Agent. The Watchdog (Phase 3) listens for these."""

    symbol: str
    severity: Severity
    check: str
    message: str
    count: int = 1
    first_ts: datetime | None = None
    last_ts: datetime | None = None


@dataclass(frozen=True)
class DataUpdated(Event):
    symbol: str
    schema: str
    rows: int
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class Decision(Event):
    """A plain-English record of a choice an agent made (entered, skipped, moved stop, ...)."""

    agent: str
    action: str
    reason: str
    symbol: str | None = None
    ts: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)
