"""A small publish/subscribe event bus.

Phase 1 runs in a single process, so this bus is synchronous and in-memory. Later phases can swap in
a networked queue (e.g. Redis) behind the same publish/subscribe interface without changing agents.
A subscriber that crashes is logged and skipped; it never takes the publisher down with it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from tradingdesk.core.events import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type, list[Handler]] = defaultdict(list)
        self.history: list[Event] = []
        self.keep_history = True

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        self._subs[event_type].append(handler)

    def publish(self, event: Event) -> None:
        if self.keep_history:
            self.history.append(event)
        for etype, handlers in self._subs.items():
            if isinstance(event, etype):
                for h in handlers:
                    try:
                        h(event)
                    except Exception:  # a broken subscriber must not break the sender
                        log.exception("Subscriber %r failed on %s", h, event.kind)

    def of_type(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.history if isinstance(e, event_type)]
