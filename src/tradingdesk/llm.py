"""Calls to Claude (Anthropic API), with a hard monthly spending cap.

Every call asks for a JSON answer that must match a schema (structured outputs), so the agents never have to
guess what Claude meant. Each call's cost is worked out from the tokens used and added to a small ledger file;
once the month's budget is used up, no more calls are made until the next month and the agents fall back to
their rule-based behaviour.

The API key is read from the ANTHROPIC_API_KEY environment variable. Without it, Claude is simply off.

If Claude's safety checks decline a request, the API retries it on Anthropic's recommended fallback model
(`fallbacks: "default"`); a request that is still declined is treated like an error.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Dollars per million tokens: input, output (Anthropic list prices). Cache writes and reads are priced in cost_of().
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
UNKNOWN_PRICE = (10.0, 50.0)  # an unlisted model is costed at the highest rate, to stay on the safe side
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def cost_of(model: str, usage) -> float:
    pin, pout = PRICES.get(model, UNKNOWN_PRICE)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (inp * pin + cw * pin * 1.25 + cr * pin * 0.1 + out * pout) / 1e6


class SpendLedger:
    """Dollars spent on Claude per month and per purpose, kept in a JSON file."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.data: dict = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}

    @staticmethod
    def month(now: pd.Timestamp | None = None) -> str:
        return f"{(now or pd.Timestamp.now(tz='UTC')):%Y-%m}"

    def spent(self, purpose: str | None = None, now=None) -> float:
        m = self.data.get(self.month(now), {})
        return float(sum(m.values()) if purpose is None else m.get(purpose, 0.0))

    def add(self, purpose: str, dollars: float, now=None) -> None:
        m = self.data.setdefault(self.month(now), {})
        m[purpose] = round(m.get(purpose, 0.0) + dollars, 6)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2))


@dataclass
class Answer:
    data: dict | None
    cost: float = 0.0
    error: str = ""
    model: str = ""


class Claude:
    """A thin wrapper: one schema-checked JSON answer per call, within the budget."""

    def __init__(self, monthly_budget: float, ledger: SpendLedger, client=None, timeout: float = 90.0) -> None:
        self.budget = monthly_budget
        self.ledger = ledger
        self._client = client
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self._client is not None or bool(os.environ.get("ANTHROPIC_API_KEY"))

    def why_unavailable(self, purpose: str = "", share: float = 1.0) -> str:
        if not self.available:
            return "no ANTHROPIC_API_KEY is set"
        if self.ledger.spent() >= self.budget:
            return f"this month's ${self.budget:.0f} Claude budget is used up"
        if share < 1.0 and self.ledger.spent(purpose) >= self.budget * share:
            return f"{purpose} has used its {share:.0%} share of this month's Claude budget"
        return ""

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=2)
        return self._client

    def ask(self, purpose: str, model: str, effort: str, system: str, user: str, schema: dict, max_tokens: int = 8000,
            share: float = 1.0) -> Answer:
        why = self.why_unavailable(purpose, share)
        if why:
            return Answer(None, error=why)
        try:
            import anthropic
        except ImportError:  # pragma: no cover - only when the optional package is missing
            return Answer(None, error="the anthropic package is not installed")
        try:
            resp = self.client().beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError:
            return Answer(None, error="the ANTHROPIC_API_KEY was rejected")
        except anthropic.RateLimitError:
            return Answer(None, error="Claude is rate-limited right now")
        except anthropic.APIStatusError as e:
            return Answer(None, error=f"Claude API error {e.status_code}")
        except anthropic.APIConnectionError:
            return Answer(None, error="could not reach the Claude API")
        served = getattr(resp, "model", model) or model
        cost = cost_of(served, resp.usage)
        self.ledger.add(purpose, cost)
        if resp.stop_reason == "refusal":
            return Answer(None, cost, "Claude declined to answer", served)
        if resp.stop_reason == "max_tokens":
            return Answer(None, cost, "Claude's answer was cut off", served)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return Answer(json.loads(text), cost, "", served)
        except json.JSONDecodeError:
            return Answer(None, cost, "Claude's answer was not valid JSON", served)
