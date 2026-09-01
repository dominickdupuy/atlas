"""The job definition schema (spec §7, extended by D18).

Jobs are fully declarative. What a job *does* is expressed by tier-gated
fields — `steps` (deterministic tool calls), `synthesize` (one model call
over step results), `goal` (tier-3 objective), `propose` (the action
template that becomes a frozen payload) — so adding a job is configuration,
not code (D4).

Every safety rule from D7/D8 is a validator here: this model is the only way
a definition enters the system, from the scheduler, the CLI validator, and
the child process alike.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Self

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from atlas.shared.ids import JobId


class Tier(IntEnum):
    """D7. Tier 1 is the default; a job must opt in to a higher tier."""

    DETERMINISTIC = 1
    SUMMARIZE = 2
    AGENT = 3


class ExecutionMode(StrEnum):
    """D8. `read` cannot mutate; `propose` queues for approval; `write`
    executes directly and requires explicit auto_approve."""

    READ = "read"
    PROPOSE = "propose"
    WRITE = "write"


class NotifyChannel(StrEnum):
    DISPLAY = "display"
    VOICE = "voice"
    BOTH = "both"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BudgetLimits(_Frozen):
    """Per-run ceilings (D7 rationale: tier 3 is where unattended systems
    fail expensively and silently)."""

    max_tokens: int | None = Field(default=None, gt=0)
    max_wall_clock_seconds: int = Field(default=60, gt=0)
    max_tool_calls: int = Field(default=10, gt=0)


class FailurePolicy(_Frozen):
    notify: NotifyChannel = NotifyChannel.DISPLAY
    retry: int = Field(default=0, ge=0)
    escalate_after: int = Field(default=2, gt=0)


class OutputSpec(_Frozen):
    publish_to: str | None = None
    speak: bool = False


class Step(_Frozen):
    """One deterministic tool call. String args may reference earlier step
    results as {{name}} placeholders, substituted at run time."""

    tool: str
    args: dict[str, JsonValue] = Field(default_factory=dict)
    save_as: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class ProposeTemplate(_Frozen):
    """The action template for propose/write modes (tiers 1-2). Rendered
    against step results, then frozen verbatim (D16 property 1)."""

    tool: str
    args: dict[str, JsonValue] = Field(default_factory=dict)
    summary: str


class JobDefinition(_Frozen):
    id: JobId = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str
    schedule: str
    tier: Tier = Tier.DETERMINISTIC
    mode: ExecutionMode = ExecutionMode.READ
    enabled: bool = True
    auto_approve: bool = False
    approval_ttl_seconds: int | None = Field(default=None, gt=0)
    tools: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    synthesize: str | None = None
    goal: str | None = None
    propose: ProposeTemplate | None = None
    budget: BudgetLimits = BudgetLimits()
    on_failure: FailurePolicy = FailurePolicy()
    output: OutputSpec = OutputSpec()

    @field_validator("schedule")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        if not croniter.is_valid(v):
            raise ValueError(f"not a valid cron expression: {v!r}")
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Self:
        problems: list[str] = []

        # D8: write is the only mode that executes directly, and only with
        # explicit consent.
        if self.mode is ExecutionMode.WRITE and not self.auto_approve:
            problems.append("mode 'write' requires explicit auto_approve: true (D8)")
        if self.mode is not ExecutionMode.WRITE and self.auto_approve:
            problems.append("auto_approve is only meaningful for mode 'write'")
        if self.mode is ExecutionMode.PROPOSE and self.approval_ttl_seconds is None:
            problems.append("mode 'propose' requires approval_ttl_seconds (D16 TTL)")

        # Tier gates (D18): what a job does must match how much autonomy it
        # declared.
        if self.tier is Tier.DETERMINISTIC:
            if self.synthesize is not None:
                problems.append("tier 1 forbids 'synthesize' (no model involvement, D7)")
            if self.goal is not None:
                problems.append("tier 1 forbids 'goal' (no model involvement, D7)")
            if not self.steps:
                problems.append("tier 1 requires at least one step")
        elif self.tier is Tier.SUMMARIZE:
            if self.synthesize is None:
                problems.append("tier 2 requires 'synthesize' (the one model call)")
            if self.goal is not None:
                problems.append("tier 2 forbids 'goal' (that is tier 3)")
            if not self.steps:
                problems.append("tier 2 requires steps to gather what the model summarizes")
            if self.budget.max_tokens is None:
                problems.append("tier 2 requires budget.max_tokens")
        else:  # Tier.AGENT
            if self.goal is None:
                problems.append("tier 3 requires 'goal'")
            if self.synthesize is not None:
                problems.append("tier 3 forbids 'synthesize' (the agent loop owns its calls)")
            if self.budget.max_tokens is None:
                problems.append("tier 3 requires budget.max_tokens")
            if self.propose is not None:
                problems.append("tier 3 forbids a static 'propose' template (the agent proposes)")
            if self.mode is ExecutionMode.WRITE:
                # Guardrails ship before the capability (spec §10): an agent
                # loop with unattended write access is exactly the failure
                # mode D8 exists to prevent.
                problems.append("tier 3 with mode 'write' is not supported; use 'propose'")

        # The action template exists exactly when a tier 1-2 job can act.
        if self.tier is not Tier.AGENT:
            if self.mode is ExecutionMode.READ and self.propose is not None:
                problems.append("mode 'read' forbids a 'propose' template (read cannot mutate, D8)")
            if self.mode in (ExecutionMode.PROPOSE, ExecutionMode.WRITE) and self.propose is None:
                problems.append(f"mode '{self.mode}' requires a 'propose' action template")

        # Every referenced tool must be allowlisted (spec §7).
        referenced = {step.tool for step in self.steps}
        if self.propose is not None:
            referenced.add(self.propose.tool)
        missing = sorted(referenced - set(self.tools))
        if missing:
            problems.append(f"tools referenced but not allowlisted: {', '.join(missing)}")

        if problems:
            raise ValueError("; ".join(problems))
        return self
