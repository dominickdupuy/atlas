"""Ports of the approvals context."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pihome.approvals.domain.approval import Approval, ApprovalState
from pihome.connectors.domain.tools import ToolResult
from pihome.jobs.domain.run import ProposedAction
from pihome.shared.ids import ApprovalId, JobId


class ApprovalRepository(Protocol):
    async def add(self, approval: Approval) -> None: ...

    async def get(self, approval_id: ApprovalId) -> Approval | None: ...

    async def pending(self) -> list[Approval]: ...

    async def pending_due(self, now: datetime) -> list[Approval]: ...

    async def transition(self, approval: Approval, expected: ApprovalState) -> bool:
        """Conditional update: persist `approval` only if the stored row is
        still in `expected` state. False means a concurrent decision won —
        the caller must reload and treat its own attempt as a replay
        (D16 idempotency, enforced at the storage boundary)."""
        ...


class FrozenPayloadExecutor(Protocol):
    """Executes an approved frozen payload verbatim (D16 property 1) through
    the connectors gateway with the owning job's allowlist."""

    async def execute(self, job_id: JobId, action: ProposedAction) -> ToolResult: ...
