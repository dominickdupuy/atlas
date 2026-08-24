"""DecideApprovalService: the D16 decision flow.

Validate → idempotent transition (conditional UPDATE) → execute the frozen
payload if approved → publish. The three HTTP-visible outcomes are applied,
replay, and expired; the router maps them to 200/200/410.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pihome.approvals.application.ports import ApprovalRepository, FrozenPayloadExecutor
from pihome.approvals.domain.approval import (
    Applied,
    Approval,
    ApprovalState,
    Decision,
    JustExpired,
    Replay,
)
from pihome.approvals.domain.events import ApprovalDecided, ApprovalExpired
from pihome.shared.clock import Clock
from pihome.shared.events import InProcessEventBus
from pihome.shared.ids import ApprovalId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecideResult:
    outcome: str  # "applied" | "replay" | "expired" | "not_found"
    state: ApprovalState | None = None
    execution_error: str | None = None


class DecideApprovalService:
    def __init__(
        self,
        *,
        repo: ApprovalRepository,
        executor: FrozenPayloadExecutor,
        bus: InProcessEventBus,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._executor = executor
        self._bus = bus
        self._clock = clock

    async def decide(
        self, approval_id: ApprovalId, decision: Decision, source: str
    ) -> DecideResult:
        approval = await self._repo.get(approval_id)
        if approval is None:
            return DecideResult(outcome="not_found")

        now = self._clock.now()
        updated, outcome = approval.decide(decision, now, source)

        match outcome:
            case Replay(state=state):
                return DecideResult(outcome="replay", state=state)
            case JustExpired():
                if not await self._repo.transition(updated, expected=ApprovalState.PENDING):
                    return await self._replay_after_race(approval_id)
                await self._publish_expired(updated)
                return DecideResult(outcome="expired", state=ApprovalState.EXPIRED)
            case Applied(state=state):
                if not await self._repo.transition(updated, expected=ApprovalState.PENDING):
                    return await self._replay_after_race(approval_id)
                execution_error: str | None = None
                if state is ApprovalState.APPROVED:
                    execution_error = await self._execute_frozen(updated)
                await self._bus.publish(
                    ApprovalDecided(
                        occurred_at=now,
                        approval_id=updated.approval_id,
                        run_id=updated.run_id,
                        job_id=updated.job_id,
                        summary=updated.action.summary,
                        approved=state is ApprovalState.APPROVED,
                        execution_error=execution_error,
                    )
                )
                return DecideResult(outcome="applied", state=state, execution_error=execution_error)

    async def _execute_frozen(self, approval: Approval) -> str | None:
        """The frozen payload is executed verbatim — never re-derived (D16
        property 1). A failed execution does not un-decide the approval; it
        is surfaced on the event and the board."""
        try:
            result = await self._executor.execute(approval.job_id, approval.action)
        except Exception as exc:
            logger.exception("frozen payload execution failed for %s", approval.approval_id)
            return str(exc)
        if result.is_error:
            return str(result.content)
        return None

    async def _replay_after_race(self, approval_id: ApprovalId) -> DecideResult:
        """A concurrent decision won the conditional update — reload and
        report the winner's terminal state as a replay (double-tap safety)."""
        current = await self._repo.get(approval_id)
        state = current.state if current is not None else None
        return DecideResult(outcome="replay", state=state)

    async def _publish_expired(self, approval: Approval) -> None:
        await self._bus.publish(
            ApprovalExpired(
                occurred_at=approval.decided_at or self._clock.now(),
                approval_id=approval.approval_id,
                run_id=approval.run_id,
                job_id=approval.job_id,
                summary=approval.action.summary,
            )
        )
