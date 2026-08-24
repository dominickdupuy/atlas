"""The JobRun aggregate, its state machine, and the parent↔child contract.

RunRequest and RunReport are the wire shapes of the D14 subprocess protocol:
the parent writes one RunRequest (JSON) to the child's stdin; the child's
final stdout line is a RunReport. Secrets never travel here — the child reads
credentials from its environment.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, JsonValue

from pihome.connectors.domain.tools import TokenUsage, ToolCall
from pihome.jobs.domain.definition import JobDefinition
from pihome.shared.ids import JobId, RunId


class RunState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    TIMED_OUT = "timed_out"


_TERMINAL = {RunState.COMPLETED, RunState.FAILED, RunState.AWAITING_APPROVAL, RunState.TIMED_OUT}


class ProposedAction(BaseModel):
    """A concrete action a job wants taken: the ToolCall that will be frozen
    (D16) plus the human-readable line shown on the phone and the board."""

    model_config = ConfigDict(frozen=True)

    call: ToolCall
    summary: str


class RunReport(BaseModel):
    """What the child hands back. `status` is about the child's own
    execution; what happens next (publish / propose / write) is the parent's
    ModeGate decision, never the child's."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "error", "timed_out"]
    output: JsonValue = None
    proposals: tuple[ProposedAction, ...] = ()
    usage: TokenUsage = TokenUsage()
    tool_calls: int = 0
    error: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: RunId
    attempt: int = 1
    definition: JobDefinition


class JobRun(BaseModel):
    """Frozen aggregate with functional transitions: each transition returns
    a new instance, and illegal transitions raise rather than corrupt."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    job_id: JobId
    tier: int
    mode: str
    attempt: int = 1
    state: RunState = RunState.RUNNING
    scheduled_for: datetime | None = None
    started_at: datetime
    finished_at: datetime | None = None
    report: RunReport | None = None
    error: str | None = None

    @classmethod
    def start(
        cls,
        *,
        run_id: RunId,
        definition: JobDefinition,
        started_at: datetime,
        scheduled_for: datetime | None,
        attempt: int = 1,
    ) -> Self:
        return cls(
            run_id=run_id,
            job_id=definition.id,
            tier=int(definition.tier),
            mode=str(definition.mode),
            attempt=attempt,
            started_at=started_at,
            scheduled_for=scheduled_for,
        )

    def _finish(self, state: RunState, finished_at: datetime, **updates: object) -> Self:
        if self.state in _TERMINAL:
            raise ValueError(f"run {self.run_id} already terminal ({self.state})")
        return self.model_copy(update={"state": state, "finished_at": finished_at, **updates})

    def completed(self, report: RunReport, at: datetime) -> Self:
        return self._finish(RunState.COMPLETED, at, report=report)

    def failed(self, error: str, at: datetime, report: RunReport | None = None) -> Self:
        return self._finish(RunState.FAILED, at, error=error, report=report)

    def timed_out(self, at: datetime) -> Self:
        return self._finish(RunState.TIMED_OUT, at, error="wall clock budget exceeded")

    def awaiting_approval(self, report: RunReport, at: datetime) -> Self:
        return self._finish(RunState.AWAITING_APPROVAL, at, report=report)

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL


class Trigger(StrEnum):
    """How a run came to be. Scheduled runs fire from cron; manual runs come
    from the API (dashboard/testing)."""

    SCHEDULE = "schedule"
    MANUAL = "manual"
