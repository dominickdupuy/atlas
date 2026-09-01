"""Approval lifecycle events → atlas/jobs/<id>/approved|rejected|expired (D16)."""

from __future__ import annotations

from atlas.shared.events import DomainEvent
from atlas.shared.ids import ApprovalId, JobId, RunId


class ApprovalEvent(DomainEvent):
    approval_id: ApprovalId
    run_id: RunId
    job_id: JobId
    summary: str


class ApprovalRequested(ApprovalEvent):
    expires_at_iso: str


class ApprovalDecided(ApprovalEvent):
    approved: bool
    execution_error: str | None = None
    """Set when the frozen payload was approved but its execution failed;
    the decision stands, the failure is surfaced."""


class ApprovalExpired(ApprovalEvent):
    pass
