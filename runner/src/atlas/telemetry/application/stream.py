"""EventStream: per-SSE-client fan-out of domain events (D17).

Feeds off the in-process bus, not MQTT — the dashboard keeps updating when
the broker is down. A slow or stuck client loses events rather than
back-pressuring the system; on reconnect the page refetches full state, so
nothing is missed for long.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from atlas.shared.events import DomainEvent, InProcessEventBus

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 64


class EventStream:
    def __init__(self, bus: InProcessEventBus) -> None:
        self._queues: set[asyncio.Queue[DomainEvent]] = set()
        bus.subscribe(self._on_event)

    async def _on_event(self, event: DomainEvent) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("dropping event for a slow SSE client")

    async def subscribe(self) -> AsyncIterator[DomainEvent]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._queues.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            with contextlib.suppress(KeyError):
                self._queues.remove(queue)

    @property
    def client_count(self) -> int:
        return len(self._queues)
