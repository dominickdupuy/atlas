"""Domain events and the in-process bus.

The bus is the cross-context communication channel (D18) and the single
source feeding both MQTT (D6, events out) and the dashboard's SSE stream —
which is why the board keeps working when the broker is down.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    occurred_at: datetime


EventHandler = Callable[[DomainEvent], Awaitable[None]]


class InProcessEventBus:
    """Async fan-out to all subscribers; handlers filter by isinstance.

    A handler failure is logged and swallowed: an event consumer must never
    break the producer's flow (a broken display should not fail a job).
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("event handler failed on %s", type(event).__name__)
