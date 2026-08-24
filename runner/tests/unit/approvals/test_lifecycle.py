"""The Approval aggregate: D16's security properties as unit tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from pihome.approvals.domain.approval import (
    Applied,
    Approval,
    ApprovalState,
    Decision,
    JustExpired,
    Replay,
)
from pihome.shared.clock import FrozenClock
from pihome.shared.ids import JobId, RunId, new_approval_id
from tests.factories import proposal


def _pending(clock: FrozenClock, ttl_seconds: int = 3600) -> Approval:
    now = clock.now()
    return Approval(
        approval_id=new_approval_id(),
        run_id=RunId("run-1"),
        job_id=JobId("lights-out"),
        action=proposal(),
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


def test_approve_within_ttl(clock: FrozenClock) -> None:
    approval = _pending(clock)
    clock.advance(60)
    decided, outcome = approval.decide(Decision.APPROVE, clock.now(), source="test")
    assert outcome == Applied(ApprovalState.APPROVED)
    assert decided.state is ApprovalState.APPROVED
    assert decided.decided_at == clock.now()
    assert decided.decision_source == "test"


def test_reject_within_ttl(clock: FrozenClock) -> None:
    approval = _pending(clock)
    decided, outcome = approval.decide(Decision.REJECT, clock.now(), source="test")
    assert outcome == Applied(ApprovalState.REJECTED)
    assert decided.state is ApprovalState.REJECTED


def test_double_decide_is_a_replay_not_a_second_execution(clock: FrozenClock) -> None:
    approval = _pending(clock)
    decided, _ = approval.decide(Decision.APPROVE, clock.now(), source="test")
    again, outcome = decided.decide(Decision.APPROVE, clock.now(), source="test")
    assert outcome == Replay(ApprovalState.APPROVED)
    assert again is decided  # unchanged, nothing re-executed


def test_conflicting_replay_reports_the_original_outcome(clock: FrozenClock) -> None:
    approval = _pending(clock)
    decided, _ = approval.decide(Decision.REJECT, clock.now(), source="test")
    _, outcome = decided.decide(Decision.APPROVE, clock.now(), source="test")
    assert outcome == Replay(ApprovalState.REJECTED)


def test_decide_after_ttl_expires_instead_of_executing(clock: FrozenClock) -> None:
    approval = _pending(clock, ttl_seconds=3600)
    clock.advance(3601)
    decided, outcome = approval.decide(Decision.APPROVE, clock.now(), source="test")
    assert isinstance(outcome, JustExpired)
    assert decided.state is ApprovalState.EXPIRED


def test_decide_exactly_at_expiry_is_expired(clock: FrozenClock) -> None:
    approval = _pending(clock, ttl_seconds=3600)
    clock.advance(3600)
    _, outcome = approval.decide(Decision.APPROVE, clock.now(), source="test")
    assert isinstance(outcome, JustExpired)


def test_sweep_expire(clock: FrozenClock) -> None:
    approval = _pending(clock)
    clock.advance(7200)
    expired = approval.expire(clock.now())
    assert expired.state is ApprovalState.EXPIRED
    assert expired.decision_source == "sweep"


def test_sweep_cannot_expire_a_decided_approval(clock: FrozenClock) -> None:
    approval = _pending(clock)
    decided, _ = approval.decide(Decision.APPROVE, clock.now(), source="test")
    with pytest.raises(ValueError, match="cannot expire"):
        decided.expire(clock.now())


def test_frozen_payload_is_immutable(clock: FrozenClock) -> None:
    approval = _pending(clock)
    with pytest.raises(Exception, match="frozen"):
        approval.action = proposal(summary="something else")  # type: ignore[misc]
