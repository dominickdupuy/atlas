"""Pure decision policies: the D8 mode gate and the retry rule.

ModeGate is the entire read/propose/write decision table in one function,
executed by the PARENT after the child reports — the child never decides
what happens to its own output.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.jobs.domain.definition import ExecutionMode, JobDefinition
from atlas.jobs.domain.run import ProposedAction, RunReport


@dataclass(frozen=True)
class PublishResult:
    report: RunReport
    dropped_proposals: tuple[ProposedAction, ...] = ()
    """Proposals a read-mode job produced anyway. They are never executed —
    read cannot mutate (D8) — but they are surfaced as a loud warning
    because they indicate a misconfigured or misbehaving job."""


@dataclass(frozen=True)
class ProposeApproval:
    report: RunReport
    proposals: tuple[ProposedAction, ...]


@dataclass(frozen=True)
class ExecuteWrite:
    report: RunReport
    actions: tuple[ProposedAction, ...]


ModeOutcome = PublishResult | ProposeApproval | ExecuteWrite


def route(definition: JobDefinition, report: RunReport) -> ModeOutcome:
    match definition.mode:
        case ExecutionMode.READ:
            return PublishResult(report=report, dropped_proposals=report.proposals)
        case ExecutionMode.PROPOSE:
            if not report.proposals:
                return PublishResult(report=report)
            return ProposeApproval(report=report, proposals=report.proposals)
        case ExecutionMode.WRITE:
            # auto_approve: true is guaranteed by the definition validator
            # (D8); re-asserted here because this is the last gate before a
            # mutation.
            if not definition.auto_approve:
                raise AssertionError("write mode without auto_approve reached the gate")
            return ExecuteWrite(report=report, actions=report.proposals)


def should_retry(definition: JobDefinition, attempt: int) -> bool:
    """attempt is 1-based; retry: 1 means one re-run after the first failure."""
    return attempt <= definition.on_failure.retry
