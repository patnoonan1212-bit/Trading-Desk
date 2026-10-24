"""Logging: readable lines on screen, JSON lines on disk, and a separate decisions log.

decisions.jsonl holds every agent decision with its plain-English reason, so any trade (or skipped trade)
can be explained after the fact.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import DataQualityIssue, Decision, Event


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )


def setup_logging(level: str = "INFO", path: str | Path | None = "logs") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    if path:
        Path(path).mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(Path(path) / "desk.jsonl")
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)


def _default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def attach_decision_log(bus: EventBus, path: str | Path = "logs") -> Path:
    """Write every Decision and DataQualityIssue on the bus to decisions.jsonl."""
    Path(path).mkdir(parents=True, exist_ok=True)
    out = Path(path) / "decisions.jsonl"

    def write(e: Event) -> None:
        with open(out, "a") as f:
            f.write(json.dumps(e.to_dict(), default=_default) + "\n")

    bus.subscribe(Decision, write)
    bus.subscribe(DataQualityIssue, write)
    return out
