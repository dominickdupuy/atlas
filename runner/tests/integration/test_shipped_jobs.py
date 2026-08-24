"""The job definitions this repo actually ships must validate — the same
check `pihome validate-jobs` and CI run."""

from __future__ import annotations

from pathlib import Path

import pytest

from pihome.jobs.application.catalog import JobCatalog
from pihome.jobs.infrastructure.yaml_source import JobFileError, YamlJobDefinitionSource

REPO_JOBS = Path(__file__).parents[2].parent / "jobs"
BAD_JOBS = Path(__file__).parent.parent / "fixtures" / "bad-jobs"


def test_shipped_jobs_validate() -> None:
    catalog = JobCatalog(YamlJobDefinitionSource(REPO_JOBS))
    catalog.load()
    ids = sorted(job.id for job in catalog.all_jobs)
    assert ids == ["calendar-today", "conflict-finder", "morning-briefing"]
    # The tier-3 example ships disabled (spec §10: riskiest capability last).
    by_id = {str(job.id): job for job in catalog.all_jobs}
    assert by_id["conflict-finder"].enabled is False


def test_invalid_job_file_fails_loudly_with_its_path() -> None:
    source = YamlJobDefinitionSource(BAD_JOBS)
    with pytest.raises(JobFileError, match=r"invalid\.yaml"):
        source.load_all()
