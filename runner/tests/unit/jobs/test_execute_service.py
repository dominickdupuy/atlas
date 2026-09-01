"""ExecuteJobService against fakes: the §6.2 flow and the terminal-event
guarantee — every path ends in exactly one terminal event."""

from __future__ import annotations

from atlas.jobs.application.execute_job import ExecuteJobService
from atlas.jobs.application.ports import JobProcessCrash, JobProcessTimeout
from atlas.jobs.domain.events import (
    JobRunAwaitingApproval,
    JobRunCompleted,
    JobRunFailed,
    JobRunStarted,
)
from atlas.jobs.domain.run import RunReport, RunState
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus
from tests.factories import (
    ok_report,
    proposal,
    propose_tier1_definition,
    tier1_definition,
    tier2_definition,
    usage,
)
from tests.fakes import (
    EventRecorder,
    FakeApprovalRequester,
    FakeBudgetGate,
    FakeLauncher,
    FakeWriteExecutor,
    InMemoryRunRepo,
)


def _service(
    launcher: FakeLauncher,
    bus: InProcessEventBus,
    clock: FrozenClock,
    *,
    budget: FakeBudgetGate | None = None,
    approvals: FakeApprovalRequester | None = None,
    writes: FakeWriteExecutor | None = None,
    repo: InMemoryRunRepo | None = None,
) -> tuple[ExecuteJobService, InMemoryRunRepo]:
    repo = repo or InMemoryRunRepo()
    service = ExecuteJobService(
        repo=repo,
        launcher=launcher,
        budget=budget or FakeBudgetGate(),
        approvals=approvals or FakeApprovalRequester(),
        writes=writes or FakeWriteExecutor(),
        bus=bus,
        clock=clock,
    )
    return service, repo


async def test_read_job_completes(bus: InProcessEventBus, clock: FrozenClock) -> None:
    recorder = EventRecorder(bus)
    service, repo = _service(FakeLauncher(ok_report()), bus, clock)
    run = await service.execute(tier1_definition(), scheduled_for=None)
    assert run.state is RunState.COMPLETED
    assert repo.runs[run.run_id].state is RunState.COMPLETED
    assert len(recorder.of_type(JobRunStarted)) == 1
    assert len(recorder.of_type(JobRunCompleted)) == 1


async def test_completed_event_carries_publish_to_override(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    recorder = EventRecorder(bus)
    definition = tier1_definition(output={"publish_to": "atlas/custom/topic"})
    service, _ = _service(FakeLauncher(ok_report()), bus, clock)
    await service.execute(definition, scheduled_for=None)
    completed = recorder.of_type(JobRunCompleted)[0]
    assert isinstance(completed, JobRunCompleted)
    assert completed.publish_to == "atlas/custom/topic"


async def test_propose_job_lands_in_approval_queue(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    recorder = EventRecorder(bus)
    approvals = FakeApprovalRequester()
    definition = propose_tier1_definition()
    report = ok_report(proposals=[proposal()])
    service, _repo = _service(FakeLauncher(report), bus, clock, approvals=approvals)
    run = await service.execute(definition, scheduled_for=None)
    assert run.state is RunState.AWAITING_APPROVAL
    assert len(approvals.requests) == 1
    _, _, action, ttl = approvals.requests[0]
    assert ttl == 3600
    waiting = recorder.of_type(JobRunAwaitingApproval)[0]
    assert isinstance(waiting, JobRunAwaitingApproval)
    assert waiting.summaries == (action.summary,)


async def test_budget_preflight_blocks_tier2(bus: InProcessEventBus, clock: FrozenClock) -> None:
    recorder = EventRecorder(bus)
    launcher = FakeLauncher(ok_report())
    service, _ = _service(
        launcher, bus, clock, budget=FakeBudgetGate(allowed=False, reason="ceiling")
    )
    run = await service.execute(tier2_definition(), scheduled_for=None)
    assert run.state is RunState.FAILED
    assert launcher.requests == []  # never spawned
    failed = recorder.of_type(JobRunFailed)[0]
    assert isinstance(failed, JobRunFailed)
    assert "ceiling" in failed.error


async def test_tier1_skips_budget_preflight(bus: InProcessEventBus, clock: FrozenClock) -> None:
    service, _ = _service(
        FakeLauncher(ok_report()), bus, clock, budget=FakeBudgetGate(allowed=False)
    )
    run = await service.execute(tier1_definition(), scheduled_for=None)
    assert run.state is RunState.COMPLETED


async def test_usage_is_recorded(bus: InProcessEventBus, clock: FrozenClock) -> None:
    budget = FakeBudgetGate()
    report = ok_report(output="briefing text", usage=usage(1000, 200))
    service, _ = _service(FakeLauncher(report), bus, clock, budget=budget)
    await service.execute(tier2_definition(), scheduled_for=None)
    assert len(budget.recorded) == 1
    assert budget.recorded[0][1].total == 1200


async def test_crash_becomes_failed(bus: InProcessEventBus, clock: FrozenClock) -> None:
    recorder = EventRecorder(bus)
    service, _ = _service(FakeLauncher(JobProcessCrash("exit 1")), bus, clock)
    run = await service.execute(tier1_definition(), scheduled_for=None)
    assert run.state is RunState.FAILED
    assert len(recorder.of_type(JobRunFailed)) == 1


async def test_timeout_becomes_timed_out_and_publishes_failed(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    recorder = EventRecorder(bus)
    service, _ = _service(FakeLauncher(JobProcessTimeout("too slow")), bus, clock)
    run = await service.execute(tier1_definition(), scheduled_for=None)
    assert run.state is RunState.TIMED_OUT
    assert len(recorder.of_type(JobRunFailed)) == 1


async def test_error_report_retries_once_then_succeeds(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    recorder = EventRecorder(bus)
    launcher = FakeLauncher(RunReport(status="error", error="flaky"), ok_report())
    definition = tier1_definition(on_failure={"retry": 1})
    service, repo = _service(launcher, bus, clock)
    run = await service.execute(definition, scheduled_for=None)
    assert run.state is RunState.COMPLETED
    assert run.attempt == 2
    assert len(launcher.requests) == 2
    # Both the failed first attempt and the completed retry were recorded.
    assert len(recorder.of_type(JobRunFailed)) == 1
    assert len(recorder.of_type(JobRunCompleted)) == 1
    assert len(repo.runs) == 2


async def test_escalation_flag_after_consecutive_failures(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    recorder = EventRecorder(bus)
    definition = tier1_definition(on_failure={"retry": 0, "escalate_after": 2})
    launcher = FakeLauncher(
        RunReport(status="error", error="1"), RunReport(status="error", error="2")
    )
    service, _repo = _service(launcher, bus, clock)
    await service.execute(definition, scheduled_for=None)
    clock.advance(60)
    await service.execute(definition, scheduled_for=None)
    failures = recorder.of_type(JobRunFailed)
    assert isinstance(failures[0], JobRunFailed) and failures[0].escalate is False
    assert isinstance(failures[1], JobRunFailed) and failures[1].escalate is True


async def test_write_mode_executes_actions(bus: InProcessEventBus, clock: FrozenClock) -> None:
    from atlas.jobs.domain.definition import JobDefinition
    from tests.factories import tier1_spec

    definition = JobDefinition.model_validate(
        tier1_spec(
            mode="write",
            auto_approve=True,
            tools=["google-calendar.list_events", "home-assistant.turn_off"],
            propose={"tool": "home-assistant.turn_off", "summary": "lights out"},
        )
    )
    writes = FakeWriteExecutor()
    report = ok_report(proposals=[proposal()])
    service, _ = _service(FakeLauncher(report), bus, clock, writes=writes)
    run = await service.execute(definition, scheduled_for=None)
    assert run.state is RunState.COMPLETED
    assert len(writes.executed) == 1


async def test_write_action_failure_fails_the_run(
    bus: InProcessEventBus, clock: FrozenClock
) -> None:
    from atlas.jobs.domain.definition import JobDefinition
    from tests.factories import tier1_spec

    definition = JobDefinition.model_validate(
        tier1_spec(
            mode="write",
            auto_approve=True,
            tools=["google-calendar.list_events", "home-assistant.turn_off"],
            propose={"tool": "home-assistant.turn_off", "summary": "lights out"},
        )
    )
    writes = FakeWriteExecutor(is_error=True)
    report = ok_report(proposals=[proposal()])
    service, _ = _service(FakeLauncher(report), bus, clock, writes=writes)
    run = await service.execute(definition, scheduled_for=None)
    assert run.state is RunState.FAILED
