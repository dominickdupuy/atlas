"""Test-only fakes (dev-profile stubs live in
pihome.connectors.infrastructure.stubs and are reused directly)."""

from __future__ import annotations

from pihome.connectors.application.ports import Notification
from pihome.connectors.domain.tools import TokenUsage, ToolResult
from pihome.jobs.application.ports import (
    BudgetDecision,
    JobProcessCrash,
    JobProcessTimeout,
    JobRunRepository,
)
from pihome.jobs.domain.definition import JobDefinition
from pihome.jobs.domain.run import JobRun, ProposedAction, RunReport, RunRequest, RunState
from pihome.shared.events import DomainEvent, InProcessEventBus
from pihome.shared.ids import ApprovalId, JobId, RunId


class EventRecorder:
    """Subscribes to the in-process bus and keeps everything it hears."""

    def __init__(self, bus: InProcessEventBus) -> None:
        self.events: list[DomainEvent] = []
        bus.subscribe(self._collect)

    async def _collect(self, event: DomainEvent) -> None:
        self.events.append(event)

    def of_type(self, event_type: type[DomainEvent]) -> list[DomainEvent]:
        return [event for event in self.events if isinstance(event, event_type)]


class FakeNotifier:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def notify(self, notification: Notification) -> None:
        self.notifications.append(notification)


class FakeLauncher:
    """Scripted child process: returns reports in order, or raises."""

    def __init__(self, *outcomes: RunReport | Exception) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[RunRequest] = []

    async def launch(self, request: RunRequest) -> RunReport:
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBudgetGate:
    def __init__(self, allowed: bool = True, reason: str = "") -> None:
        self._decision = BudgetDecision(allowed=allowed, reason=reason)
        self.recorded: list[tuple[RunId, TokenUsage]] = []

    async def preflight(self, definition: JobDefinition) -> BudgetDecision:
        return self._decision

    async def record(self, definition: JobDefinition, run_id: RunId, usage: TokenUsage) -> None:
        self.recorded.append((run_id, usage))


class FakeApprovalRequester:
    def __init__(self) -> None:
        self.requests: list[tuple[RunId, JobId, ProposedAction, int]] = []

    async def request(
        self, *, run_id: RunId, job_id: JobId, action: ProposedAction, ttl_seconds: int
    ) -> ApprovalId:
        self.requests.append((run_id, job_id, action, ttl_seconds))
        return ApprovalId(f"approval-{len(self.requests)}")


class InMemoryRunRepo:
    def __init__(self) -> None:
        self.runs: dict[RunId, JobRun] = {}

    async def add(self, run: JobRun) -> None:
        self.runs[run.run_id] = run

    async def update(self, run: JobRun) -> None:
        self.runs[run.run_id] = run

    async def get(self, run_id: RunId) -> JobRun | None:
        return self.runs.get(run_id)

    async def recent(self, limit: int) -> list[JobRun]:
        ordered = sorted(self.runs.values(), key=lambda run: run.started_at, reverse=True)
        return ordered[:limit]

    async def recent_for_job(self, job_id: JobId, limit: int) -> list[JobRun]:
        return [run for run in await self.recent(len(self.runs)) if run.job_id == job_id][:limit]

    async def consecutive_failures(self, job_id: JobId) -> int:
        streak = 0
        for run in await self.recent_for_job(job_id, len(self.runs)):
            if run.state is RunState.RUNNING:
                continue
            if run.state in (RunState.FAILED, RunState.TIMED_OUT):
                streak += 1
            else:
                break
        return streak


class FakeWriteExecutor:
    def __init__(self, is_error: bool = False) -> None:
        self._is_error = is_error
        self.executed: list[ProposedAction] = []

    async def execute(self, definition: JobDefinition, action: ProposedAction) -> ToolResult:
        self.executed.append(action)
        return ToolResult(
            tool=action.call.tool,
            content="boom" if self._is_error else "ok",
            is_error=self._is_error,
        )


__all__ = [
    "EventRecorder",
    "FakeApprovalRequester",
    "FakeBudgetGate",
    "FakeLauncher",
    "FakeNotifier",
    "FakeWriteExecutor",
    "JobProcessCrash",
    "JobProcessTimeout",
    "JobRunRepository",
]
