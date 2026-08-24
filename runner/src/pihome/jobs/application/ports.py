"""Ports of the jobs context. The cross-context collaborators (budget gate,
approval requester, write executor) are declared here as protocols and
implemented by the other contexts' application services — dependency
inversion keeps this context extractable (D18)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pihome.connectors.domain.tools import TokenUsage, ToolResult
from pihome.jobs.domain.definition import JobDefinition
from pihome.jobs.domain.run import JobRun, ProposedAction, RunReport, RunRequest
from pihome.shared.ids import ApprovalId, JobId, RunId


class JobRunRepository(Protocol):
    async def add(self, run: JobRun) -> None: ...

    async def update(self, run: JobRun) -> None: ...

    async def get(self, run_id: RunId) -> JobRun | None: ...

    async def recent(self, limit: int) -> list[JobRun]: ...

    async def recent_for_job(self, job_id: JobId, limit: int) -> list[JobRun]: ...

    async def consecutive_failures(self, job_id: JobId) -> int: ...


class JobDefinitionSource(Protocol):
    def load_all(self) -> list[JobDefinition]: ...


class JobProcessTimeout(Exception):
    """The child exceeded its wall clock budget and was killed (D14)."""


class JobProcessCrash(Exception):
    """The child exited abnormally or produced an unreadable report. The
    parent treats this as failed — the terminal-event guarantee (spec §8)
    must survive a child crash."""


class JobProcessLauncher(Protocol):
    async def launch(self, request: RunRequest) -> RunReport:
        """Raises JobProcessTimeout or JobProcessCrash."""
        ...


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""


class BudgetGate(Protocol):
    """Implemented by the budget context (spec §8: pre-flight before any
    model-calling spawn; ledger write after)."""

    async def preflight(self, definition: JobDefinition) -> BudgetDecision: ...

    async def record(self, definition: JobDefinition, run_id: RunId, usage: TokenUsage) -> None: ...


class ApprovalRequester(Protocol):
    """Implemented by the approvals context (D8/D16)."""

    async def request(
        self, *, run_id: RunId, job_id: JobId, action: ProposedAction, ttl_seconds: int
    ) -> ApprovalId: ...


class WriteExecutor(Protocol):
    """Executes an auto-approved write action (D8 `write` mode) through the
    connectors gateway with the job's own allowlist."""

    async def execute(self, definition: JobDefinition, action: ProposedAction) -> ToolResult: ...
