"""The Approval aggregate: D16's four required properties as pure logic.

1. Frozen payload — `action` is serialized at proposal time and executed
   verbatim on approval; nothing here re-derives it.
2. Idempotency — deciding an already-decided approval returns the existing
   terminal state (Replay), never re-executes.
3. TTL — expiry is checked at decision time against the caller's clock, not
   only by the background sweep. An approval tapped three days later does
   not run.
4. Persistence is the repository's job; this aggregate is the invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict

from atlas.jobs.domain.run import ProposedAction
from atlas.shared.ids import ApprovalId, JobId, RunId


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class Applied:
    """The decision took effect just now."""

    state: ApprovalState


@dataclass(frozen=True)
class Replay:
    """Already terminal — a double tap on a flaky connection. The caller
    gets the existing state and nothing executes again (D16 property 2)."""

    state: ApprovalState


@dataclass(frozen=True)
class JustExpired:
    """The tap arrived after expires_at: recorded as expired, not executed
    (D16 property 3)."""


DecisionOutcome = Applied | Replay | JustExpired


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: ApprovalId
    run_id: RunId
    job_id: JobId
    action: ProposedAction
    state: ApprovalState = ApprovalState.PENDING
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decision_source: str | None = None

    def decide(
        self, decision: Decision, now: datetime, source: str
    ) -> tuple[Self, DecisionOutcome]:
        if self.state is not ApprovalState.PENDING:
            return self, Replay(self.state)
        if now >= self.expires_at:
            expired = self.model_copy(
                update={
                    "state": ApprovalState.EXPIRED,
                    "decided_at": now,
                    "decision_source": source,
                }
            )
            return expired, JustExpired()
        new_state = (
            ApprovalState.APPROVED if decision is Decision.APPROVE else ApprovalState.REJECTED
        )
        decided = self.model_copy(
            update={"state": new_state, "decided_at": now, "decision_source": source}
        )
        return decided, Applied(new_state)

    def expire(self, now: datetime) -> Self:
        """Background-sweep expiry (belt and braces; decide() is authoritative)."""
        if self.state is not ApprovalState.PENDING:
            raise ValueError(f"cannot expire approval in state {self.state}")
        return self.model_copy(
            update={"state": ApprovalState.EXPIRED, "decided_at": now, "decision_source": "sweep"}
        )
