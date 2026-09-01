"""Domain event → MQTT envelope mapping. The ONLY place the D6 topic
namespace strings live.

    atlas/jobs/<job_id>/started|completed|failed|awaiting_approval
    atlas/jobs/<job_id>/approved|rejected|expired
    atlas/display/mode
    atlas/budget/status
    atlas/system/health
"""

from __future__ import annotations

from pydantic import JsonValue

from atlas.approvals.domain.events import ApprovalDecided, ApprovalExpired, ApprovalRequested
from atlas.budget.domain.events import BudgetStatusChanged, DailyCeilingReached
from atlas.jobs.domain.events import (
    JobRunAwaitingApproval,
    JobRunCompleted,
    JobRunFailed,
    JobRunStarted,
)
from atlas.shared.events import DomainEvent
from atlas.telemetry.domain.envelope import EventEnvelope

ROOT = "atlas"


def job_topic(job_id: str, suffix: str) -> str:
    return f"{ROOT}/jobs/{job_id}/{suffix}"


DISPLAY_MODE_TOPIC = f"{ROOT}/display/mode"
BUDGET_STATUS_TOPIC = f"{ROOT}/budget/status"
SYSTEM_HEALTH_TOPIC = f"{ROOT}/system/health"


class DisplayModeChanged(DomainEvent):
    """Owned here: display mode is derived presentation state, not a fact of
    any other context. States per D11."""

    mode: str


class SystemHealth(DomainEvent):
    healthy: bool
    detail: dict[str, JsonValue]


def envelope_for(event: DomainEvent) -> EventEnvelope | None:
    """None means the event has no MQTT representation (internal-only)."""
    payload: dict[str, JsonValue]
    match event:
        case JobRunStarted():
            topic = job_topic(event.job_id, "started")
            payload = {"run_id": event.run_id, "tier": event.tier, "attempt": event.attempt}
        case JobRunCompleted():
            topic = event.publish_to or job_topic(event.job_id, "completed")
            payload = {"run_id": event.run_id, "output": event.output}
        case JobRunFailed():
            topic = job_topic(event.job_id, "failed")
            payload = {
                "run_id": event.run_id,
                "error": event.error,
                "consecutive_failures": event.consecutive_failures,
                "escalate": event.escalate,
            }
        case JobRunAwaitingApproval():
            topic = job_topic(event.job_id, "awaiting_approval")
            payload = {
                "run_id": event.run_id,
                "approval_ids": list(event.approval_ids),
                "summaries": list(event.summaries),
            }
        case ApprovalDecided():
            topic = job_topic(event.job_id, "approved" if event.approved else "rejected")
            payload = {
                "approval_id": event.approval_id,
                "summary": event.summary,
                "execution_error": event.execution_error,
            }
        case ApprovalExpired():
            topic = job_topic(event.job_id, "expired")
            payload = {"approval_id": event.approval_id, "summary": event.summary}
        case ApprovalRequested():
            # The queue itself was already announced by JobRunAwaitingApproval;
            # individual requests are internal (they drive the notifier and
            # the board).
            return None
        case BudgetStatusChanged() | DailyCeilingReached():
            topic = BUDGET_STATUS_TOPIC
            payload = dict(event.status.model_dump())
        case DisplayModeChanged():
            topic = DISPLAY_MODE_TOPIC
            payload = {"mode": event.mode}
        case SystemHealth():
            topic = SYSTEM_HEALTH_TOPIC
            payload = {"healthy": event.healthy, **event.detail}
        case _:
            return None
    return EventEnvelope(topic=topic, payload=payload, occurred_at=event.occurred_at)
