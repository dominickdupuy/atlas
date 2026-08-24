"""DisplayModeTracker: derives the D11 display mode from the event flow.

v1 knows OPS and APPROVAL_PENDING; the voice states (LISTENING / THINKING /
SPEAKING) arrive in phase 6 as externally sourced events. The pending count
is seeded from the repository at startup so a restart with a pending
approval still shows APPROVAL_PENDING.
"""

from __future__ import annotations

import logging

from pihome.approvals.domain.events import ApprovalDecided, ApprovalExpired, ApprovalRequested
from pihome.shared.clock import Clock
from pihome.shared.events import DomainEvent, InProcessEventBus
from pihome.telemetry.domain.topics import DisplayModeChanged

logger = logging.getLogger(__name__)

MODE_OPS = "OPS"
MODE_APPROVAL_PENDING = "APPROVAL_PENDING"


class DisplayModeTracker:
    def __init__(self, bus: InProcessEventBus, clock: Clock, initial_pending: int = 0) -> None:
        self._bus = bus
        self._clock = clock
        self._pending = initial_pending
        bus.subscribe(self._on_event)

    @property
    def mode(self) -> str:
        return MODE_APPROVAL_PENDING if self._pending > 0 else MODE_OPS

    def seed_pending(self, count: int) -> None:
        """Called once at startup with the persisted pending count, so a
        restart with a pending approval still shows APPROVAL_PENDING."""
        self._pending = count

    async def _on_event(self, event: DomainEvent) -> None:
        before = self.mode
        match event:
            case ApprovalRequested():
                self._pending += 1
            case ApprovalDecided() | ApprovalExpired():
                self._pending = max(0, self._pending - 1)
            case _:
                return
        if self.mode != before:
            await self._bus.publish(
                DisplayModeChanged(occurred_at=self._clock.now(), mode=self.mode)
            )
