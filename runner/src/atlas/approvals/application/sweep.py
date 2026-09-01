"""ExpireSweepService: belt-and-braces expiry (D16 property 3).

The authoritative expiry check happens at decision time in the aggregate;
this sweep exists so the board and the phone stop showing stale pendings,
not to enforce safety.
"""

from __future__ import annotations

import asyncio
import logging

from atlas.approvals.application.ports import ApprovalRepository
from atlas.approvals.domain.approval import ApprovalState
from atlas.approvals.domain.events import ApprovalExpired
from atlas.shared.clock import Clock
from atlas.shared.events import InProcessEventBus

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60.0


class ExpireSweepService:
    def __init__(self, *, repo: ApprovalRepository, bus: InProcessEventBus, clock: Clock) -> None:
        self._repo = repo
        self._bus = bus
        self._clock = clock

    async def sweep_once(self) -> int:
        now = self._clock.now()
        expired_count = 0
        for approval in await self._repo.pending_due(now):
            expired = approval.expire(now)
            if not await self._repo.transition(expired, expected=ApprovalState.PENDING):
                continue  # a decision landed between the query and the sweep
            expired_count += 1
            await self._bus.publish(
                ApprovalExpired(
                    occurred_at=now,
                    approval_id=expired.approval_id,
                    run_id=expired.run_id,
                    job_id=expired.job_id,
                    summary=expired.action.summary,
                )
            )
        if expired_count:
            logger.info("expired %d stale approval(s)", expired_count)
        return expired_count

    async def run(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("approval sweep failed; will retry")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
