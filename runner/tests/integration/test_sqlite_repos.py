"""Each SQLite repository against a real temporary database."""

from __future__ import annotations

from datetime import timedelta

from pihome.approvals.domain.approval import Approval, ApprovalState, Decision
from pihome.approvals.infrastructure.sqlite_repo import SqliteApprovalRepository
from pihome.budget.domain.ledger import LedgerEntry, UsdMicros
from pihome.budget.infrastructure.sqlite_repo import SqliteBudgetLedgerRepository
from pihome.jobs.domain.run import JobRun, RunState
from pihome.jobs.infrastructure.sqlite_run_repo import SqliteJobRunRepository
from pihome.persistence.db import Database
from pihome.shared.clock import FrozenClock
from pihome.shared.ids import JobId, new_approval_id, new_entry_id, new_run_id
from tests.factories import ok_report, proposal, tier1_definition, usage


def _run(clock: FrozenClock, definition_id: str = "calendar-today") -> JobRun:
    return JobRun.start(
        run_id=new_run_id(),
        definition=tier1_definition(id=definition_id),
        started_at=clock.now(),
        scheduled_for=None,
    )


async def test_run_repo_roundtrip(db: Database, clock: FrozenClock) -> None:
    repo = SqliteJobRunRepository(db)
    run = _run(clock)
    await repo.add(run)

    loaded = await repo.get(run.run_id)
    assert loaded is not None
    assert loaded.state is RunState.RUNNING
    assert loaded.started_at == run.started_at

    completed = run.completed(ok_report(), clock.now())
    await repo.update(completed)
    reloaded = await repo.get(run.run_id)
    assert reloaded is not None
    assert reloaded.state is RunState.COMPLETED
    assert reloaded.report == completed.report


async def test_recent_ordering_and_per_job_filter(db: Database, clock: FrozenClock) -> None:
    repo = SqliteJobRunRepository(db)
    first = _run(clock, "job-a")
    clock.advance(60)
    second = _run(clock, "job-b")
    await repo.add(first)
    await repo.add(second)

    recent = await repo.recent(10)
    assert [run.run_id for run in recent] == [second.run_id, first.run_id]
    only_a = await repo.recent_for_job(JobId("job-a"), 10)
    assert [run.run_id for run in only_a] == [first.run_id]


async def test_consecutive_failures_counts_the_streak(db: Database, clock: FrozenClock) -> None:
    repo = SqliteJobRunRepository(db)
    job_id = "flaky-job"

    ok_run = _run(clock, job_id)
    await repo.add(ok_run)
    await repo.update(ok_run.completed(ok_report(), clock.now()))
    for _ in range(2):
        clock.advance(60)
        failed = _run(clock, job_id)
        await repo.add(failed)
        await repo.update(failed.failed("boom", clock.now()))

    assert await repo.consecutive_failures(JobId(job_id)) == 2


async def _seed_run(db: Database, clock: FrozenClock) -> JobRun:
    repo = SqliteJobRunRepository(db)
    run = _run(clock, "lights-out")
    await repo.add(run)
    return run


def _approval(clock: FrozenClock, run: JobRun, ttl_seconds: int = 3600) -> Approval:
    return Approval(
        approval_id=new_approval_id(),
        run_id=run.run_id,
        job_id=run.job_id,
        action=proposal(),
        created_at=clock.now(),
        expires_at=clock.now() + timedelta(seconds=ttl_seconds),
    )


async def test_approval_repo_roundtrip_and_pending(db: Database, clock: FrozenClock) -> None:
    run = await _seed_run(db, clock)
    repo = SqliteApprovalRepository(db)
    approval = _approval(clock, run)
    await repo.add(approval)

    assert await repo.get(approval.approval_id) == approval
    assert [pending.approval_id for pending in await repo.pending()] == [approval.approval_id]

    clock.advance(7200)
    due = await repo.pending_due(clock.now())
    assert [item.approval_id for item in due] == [approval.approval_id]


async def test_conditional_transition_enforces_idempotency(
    db: Database, clock: FrozenClock
) -> None:
    run = await _seed_run(db, clock)
    repo = SqliteApprovalRepository(db)
    approval = _approval(clock, run)
    await repo.add(approval)

    decided, _ = approval.decide(Decision.APPROVE, clock.now(), source="test")
    assert await repo.transition(decided, expected=ApprovalState.PENDING) is True
    # The second writer loses: the row is no longer pending.
    rejected, _ = approval.decide(Decision.REJECT, clock.now(), source="test")
    assert await repo.transition(rejected, expected=ApprovalState.PENDING) is False

    stored = await repo.get(approval.approval_id)
    assert stored is not None
    assert stored.state is ApprovalState.APPROVED


async def test_ledger_totals_since(db: Database, clock: FrozenClock) -> None:
    repo = SqliteBudgetLedgerRepository(db)
    day_start = clock.now()

    for cost in (250_000, 750_000):
        clock.advance(3600)
        await repo.add(
            LedgerEntry(
                entry_id=new_entry_id(),
                run_id=None,
                job_id=JobId("morning-briefing"),
                model="claude-sonnet-5",
                usage=usage(),
                cost_usd_micros=UsdMicros(cost),
                recorded_at=clock.now(),
            )
        )

    assert await repo.total_since(day_start) == 1_000_000
    assert await repo.total_since(clock.now()) == 750_000  # only the last entry
