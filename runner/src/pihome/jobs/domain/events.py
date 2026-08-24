"""Job lifecycle events (D6 topic namespace maps these to MQTT)."""

from __future__ import annotations

from pydantic import JsonValue

from pihome.shared.events import DomainEvent
from pihome.shared.ids import JobId, RunId


class JobRunEvent(DomainEvent):
    job_id: JobId
    run_id: RunId


class JobRunStarted(JobRunEvent):
    tier: int
    mode: str
    attempt: int


class JobRunCompleted(JobRunEvent):
    output: JsonValue = None
    publish_to: str | None = None
    """Optional topic override from the job's output.publish_to (spec §7)."""


class JobRunFailed(JobRunEvent):
    error: str
    consecutive_failures: int = 1
    escalate: bool = False
    """True once consecutive failures reach the job's escalate_after
    threshold (spec §7 on_failure)."""


class JobRunAwaitingApproval(JobRunEvent):
    approval_ids: tuple[str, ...]
    summaries: tuple[str, ...]
