"""JobCatalog: loads and validates every definition at startup, failing fast.

A bad job file stops the service from starting rather than silently skipping
the job — a scheduler that quietly drops work is the silent failure spec §8
forbids.
"""

from __future__ import annotations

import logging

from atlas.jobs.application.ports import JobDefinitionSource
from atlas.jobs.domain.definition import JobDefinition
from atlas.shared.ids import JobId

logger = logging.getLogger(__name__)


class DuplicateJobId(Exception):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"duplicate job id: {job_id}")


class JobCatalog:
    def __init__(self, source: JobDefinitionSource) -> None:
        self._source = source
        self._jobs: dict[JobId, JobDefinition] = {}

    def load(self) -> None:
        jobs: dict[JobId, JobDefinition] = {}
        for definition in self._source.load_all():
            if definition.id in jobs:
                raise DuplicateJobId(definition.id)
            jobs[definition.id] = definition
            logger.info(
                "job %s: tier %d, mode %s, schedule %r%s",
                definition.id,
                definition.tier,
                definition.mode,
                definition.schedule,
                "" if definition.enabled else " (disabled)",
            )
        self._jobs = jobs

    @property
    def all_jobs(self) -> list[JobDefinition]:
        return list(self._jobs.values())

    @property
    def enabled_jobs(self) -> list[JobDefinition]:
        return [job for job in self._jobs.values() if job.enabled]

    def get(self, job_id: JobId) -> JobDefinition | None:
        return self._jobs.get(job_id)
