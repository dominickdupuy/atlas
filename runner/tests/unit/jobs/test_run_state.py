"""JobRun state machine: terminal means terminal."""

from __future__ import annotations

import pytest

from pihome.jobs.domain.run import JobRun, RunState
from pihome.shared.clock import FrozenClock
from pihome.shared.ids import new_run_id
from tests.factories import ok_report, tier1_definition


def _running(clock: FrozenClock) -> JobRun:
    return JobRun.start(
        run_id=new_run_id(),
        definition=tier1_definition(),
        started_at=clock.now(),
        scheduled_for=None,
    )


def test_start_is_running(clock: FrozenClock) -> None:
    run = _running(clock)
    assert run.state is RunState.RUNNING
    assert not run.is_terminal


@pytest.mark.parametrize("transition", ["completed", "failed", "timed_out", "awaiting_approval"])
def test_terminal_states_reject_further_transitions(clock: FrozenClock, transition: str) -> None:
    run = _running(clock)
    clock.advance(5)
    terminal = {
        "completed": lambda: run.completed(ok_report(), clock.now()),
        "failed": lambda: run.failed("boom", clock.now()),
        "timed_out": lambda: run.timed_out(clock.now()),
        "awaiting_approval": lambda: run.awaiting_approval(ok_report(), clock.now()),
    }[transition]()
    assert terminal.is_terminal
    assert terminal.finished_at == clock.now()
    with pytest.raises(ValueError, match="already terminal"):
        terminal.failed("again", clock.now())


def test_transitions_do_not_mutate_the_original(clock: FrozenClock) -> None:
    run = _running(clock)
    completed = run.completed(ok_report(), clock.now())
    assert run.state is RunState.RUNNING
    assert completed.state is RunState.COMPLETED
