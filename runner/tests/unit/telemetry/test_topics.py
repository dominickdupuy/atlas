"""Domain event → D6 topic mapping."""

from __future__ import annotations

from datetime import UTC, datetime

from pihome.approvals.domain.events import ApprovalDecided, ApprovalExpired, ApprovalRequested
from pihome.budget.domain.events import BudgetStatusChanged
from pihome.budget.domain.ledger import usd
from pihome.budget.domain.policy import evaluate
from pihome.jobs.domain.events import JobRunCompleted, JobRunFailed, JobRunStarted
from pihome.shared.ids import ApprovalId, JobId, RunId
from pihome.telemetry.domain.topics import DisplayModeChanged, SystemHealth, envelope_for

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
JOB = JobId("morning-briefing")
RUN = RunId("run-1")


def test_job_lifecycle_topics() -> None:
    started = envelope_for(
        JobRunStarted(occurred_at=NOW, job_id=JOB, run_id=RUN, tier=2, mode="read", attempt=1)
    )
    assert started is not None
    assert started.topic == "pihome/jobs/morning-briefing/started"

    failed = envelope_for(
        JobRunFailed(occurred_at=NOW, job_id=JOB, run_id=RUN, error="x", escalate=True)
    )
    assert failed is not None
    assert failed.topic == "pihome/jobs/morning-briefing/failed"
    assert failed.payload["escalate"] is True


def test_completed_respects_publish_to_override() -> None:
    default = envelope_for(JobRunCompleted(occurred_at=NOW, job_id=JOB, run_id=RUN))
    assert default is not None
    assert default.topic == "pihome/jobs/morning-briefing/completed"

    overridden = envelope_for(
        JobRunCompleted(occurred_at=NOW, job_id=JOB, run_id=RUN, publish_to="pihome/custom")
    )
    assert overridden is not None
    assert overridden.topic == "pihome/custom"


def test_approval_outcome_topics() -> None:
    def decided(approved: bool) -> ApprovalDecided:
        return ApprovalDecided(
            occurred_at=NOW,
            approval_id=ApprovalId("a1"),
            run_id=RUN,
            job_id=JOB,
            summary="do it",
            approved=approved,
        )

    approved = envelope_for(decided(True))
    rejected = envelope_for(decided(False))
    expired = envelope_for(
        ApprovalExpired(
            occurred_at=NOW,
            approval_id=ApprovalId("a1"),
            run_id=RUN,
            job_id=JOB,
            summary="do it",
        )
    )
    assert approved is not None and approved.topic.endswith("/approved")
    assert rejected is not None and rejected.topic.endswith("/rejected")
    assert expired is not None and expired.topic.endswith("/expired")


def test_approval_requested_is_internal_only() -> None:
    event = ApprovalRequested(
        occurred_at=NOW,
        approval_id=ApprovalId("a1"),
        run_id=RUN,
        job_id=JOB,
        summary="do it",
        expires_at_iso=NOW.isoformat(),
    )
    assert envelope_for(event) is None


def test_singleton_topics() -> None:
    budget = envelope_for(
        BudgetStatusChanged(occurred_at=NOW, status=evaluate(usd("1.00"), usd("5.00")))
    )
    mode = envelope_for(DisplayModeChanged(occurred_at=NOW, mode="OPS"))
    health = envelope_for(SystemHealth(occurred_at=NOW, healthy=True, detail={}))
    assert budget is not None and budget.topic == "pihome/budget/status"
    assert mode is not None and mode.topic == "pihome/display/mode"
    assert health is not None and health.topic == "pihome/system/health"
