"""The croniter loop's pure core: due_jobs with a FrozenClock."""

from __future__ import annotations

from datetime import datetime

from pihome.jobs.application.catalog import JobCatalog
from pihome.jobs.application.scheduler import CronScheduler
from pihome.jobs.domain.definition import JobDefinition
from pihome.shared.clock import FrozenClock
from tests.factories import tier1_definition


class ListSource:
    def __init__(self, definitions: list[JobDefinition]) -> None:
        self._definitions = definitions

    def load_all(self) -> list[JobDefinition]:
        return self._definitions


def _scheduler(clock: FrozenClock, *definitions: JobDefinition) -> CronScheduler:
    catalog = JobCatalog(ListSource(list(definitions)))
    catalog.load()

    async def on_due(definition: JobDefinition, scheduled_for: datetime) -> None:
        pass

    return CronScheduler(catalog=catalog, clock=clock, on_due=on_due, timezone="UTC")


def test_first_observation_never_fires(clock: FrozenClock) -> None:
    scheduler = _scheduler(clock, tier1_definition(schedule="*/30 * * * *"))
    assert scheduler.due_jobs(clock.now()) == []


def test_fires_when_next_fire_arrives(clock: FrozenClock) -> None:
    definition = tier1_definition(schedule="*/30 * * * *")
    scheduler = _scheduler(clock, definition)
    scheduler.due_jobs(clock.now())  # prime: next fire is 12:30 UTC
    clock.advance(30 * 60)
    due = scheduler.due_jobs(clock.now())
    assert [job.id for job, _ in due] == [definition.id]


def test_missed_fires_coalesce_to_one(clock: FrozenClock) -> None:
    definition = tier1_definition(schedule="*/30 * * * *")
    scheduler = _scheduler(clock, definition)
    scheduler.due_jobs(clock.now())
    clock.advance(3 * 60 * 60)  # slept through six fire slots
    assert len(scheduler.due_jobs(clock.now())) == 1
    # And the next fire is recomputed from now, not from the backlog.
    assert scheduler.due_jobs(clock.now()) == []


def test_disabled_jobs_never_fire(clock: FrozenClock) -> None:
    scheduler = _scheduler(clock, tier1_definition(enabled=False, schedule="* * * * *"))
    scheduler.due_jobs(clock.now())
    clock.advance(120)
    assert scheduler.due_jobs(clock.now()) == []


def test_pause_is_reported(clock: FrozenClock) -> None:
    scheduler = _scheduler(clock, tier1_definition())
    states = [scheduler.paused]
    scheduler.pause()
    states.append(scheduler.paused)
    scheduler.resume()
    states.append(scheduler.paused)
    assert states == [False, True, False]


def test_next_fires_are_sorted(clock: FrozenClock) -> None:
    early = tier1_definition(id="early", schedule="*/5 * * * *")
    late = tier1_definition(id="late", schedule="0 23 * * *")
    scheduler = _scheduler(clock, late, early)
    fires = scheduler.next_fires()
    assert [job.id for job, _ in fires] == ["early", "late"]
    assert fires[0][1] < fires[1][1]
