"""HealthService: periodic heartbeat → pihome/system/health.

Spec §8's silent-failure rule applies to the runner itself: a missing
heartbeat on the broker is how an observer notices the runner died.
"""

from __future__ import annotations

import asyncio
import logging

from pihome.shared.clock import Clock
from pihome.shared.events import InProcessEventBus
from pihome.telemetry.domain.topics import SystemHealth

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30.0


class HealthService:
    def __init__(self, *, bus: InProcessEventBus, clock: Clock) -> None:
        self._bus = bus
        self._clock = clock
        self._started_at = clock.now()

    async def beat_once(self) -> None:
        now = self._clock.now()
        await self._bus.publish(
            SystemHealth(
                occurred_at=now,
                healthy=True,
                detail={"uptime_seconds": int((now - self._started_at).total_seconds())},
            )
        )

    async def run(self) -> None:
        while True:
            try:
                await self.beat_once()
            except Exception:
                logger.exception("heartbeat failed")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
