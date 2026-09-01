"""CronScheduler: a hand-rolled croniter loop (~60 lines of logic).

Chosen over APScheduler because schedule state is fully derivable from YAML
plus the clock — jobstores and executors would be dead weight, and this loop
produces stack traces a solo maintainer can read (spec §3).

Missed-run policy: coalesce. On a late wake or restart, a job that missed
one or more fire times runs at most once, never backfills — correct for
"pull today's calendar" and every other job in this system.

The budget ceiling pause (spec §8) is `pause()`; the loop simply stops
firing until `resume()`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from atlas.jobs.application.catalog import JobCatalog
from atlas.jobs.domain.definition import JobDefinition
from atlas.shared.clock import Clock
from atlas.shared.ids import JobId

logger = logging.getLogger(__name__)

_MAX_SLEEP_SECONDS = 30.0

OnDue = Callable[[JobDefinition, datetime], Coroutine[Any, Any, None]]


class CronScheduler:
    def __init__(self, catalog: JobCatalog, clock: Clock, on_due: OnDue, timezone: str) -> None:
        self._catalog = catalog
        self._clock = clock
        self._on_due = on_due
        self._tz = ZoneInfo(timezone)
        self._paused = False
        self._next_fire: dict[JobId, datetime] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def pause(self) -> None:
        if not self._paused:
            logger.warning("scheduler paused")
        self._paused = True

    def resume(self) -> None:
        if self._paused:
            logger.info("scheduler resumed")
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def next_fires(self) -> list[tuple[JobDefinition, datetime]]:
        """Upcoming fire times for the board's schedule panel."""
        now = self._clock.now()
        upcoming = [(job, self._next_after(job, now)) for job in self._catalog.enabled_jobs]
        return sorted(upcoming, key=lambda pair: pair[1])

    def _next_after(self, job: JobDefinition, after: datetime) -> datetime:
        local = after.astimezone(self._tz)
        fire_local: datetime = croniter(job.schedule, local).get_next(datetime)
        return fire_local.astimezone(after.tzinfo)

    def due_jobs(self, now: datetime) -> list[tuple[JobDefinition, datetime]]:
        """Pure-ish core, unit-testable with FrozenClock: which jobs fire at
        `now`, updating the per-job next-fire bookkeeping (coalescing)."""
        due: list[tuple[JobDefinition, datetime]] = []
        seen: set[JobId] = set()
        for job in self._catalog.enabled_jobs:
            seen.add(job.id)
            next_fire = self._next_fire.get(job.id)
            if next_fire is None:
                self._next_fire[job.id] = self._next_after(job, now)
                continue
            if next_fire <= now:
                due.append((job, next_fire))
                # Recompute from now, not from the missed slot: run at most
                # once, never backfill.
                self._next_fire[job.id] = self._next_after(job, now)
        for job_id in set(self._next_fire) - seen:
            del self._next_fire[job_id]
        return due

    def _seconds_until_next(self, now: datetime) -> float:
        if not self._next_fire:
            return _MAX_SLEEP_SECONDS
        soonest = min(self._next_fire.values())
        return max(0.1, min((soonest - now).total_seconds(), _MAX_SLEEP_SECONDS))

    async def run(self) -> None:
        logger.info("scheduler started (%d enabled jobs)", len(self._catalog.enabled_jobs))
        while True:
            now = self._clock.now()
            if self._paused:
                await asyncio.sleep(1.0)
                continue
            for job, scheduled_for in self.due_jobs(now):
                task = asyncio.create_task(self._on_due(job, scheduled_for), name=f"job:{job.id}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            await asyncio.sleep(self._seconds_until_next(self._clock.now()))
