"""Job inspection (D16: GET /api/jobs/{id}) plus a manual trigger.

POST /api/jobs/{id}/run is an operability addition beyond the D16 list: it
runs the identical code path a cron fire would, which is how a job is
exercised before its schedule comes around — including disabled ones like
the tier-3 example.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import JsonValue

from atlas.bootstrap.container import Application
from atlas.jobs.domain.definition import JobDefinition
from atlas.jobs.domain.run import JobRun
from atlas.presentation.http.routers._deps import get_application
from atlas.shared.ids import JobId

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs")

_manual_runs: set[asyncio.Task[JobRun]] = set()


def _definition_json(definition: JobDefinition) -> dict[str, JsonValue]:
    return dict(definition.model_dump(mode="json"))


def _run_json(run: JobRun) -> dict[str, JsonValue]:
    return dict(run.model_dump(mode="json"))


@router.get("")
async def list_jobs(
    application: Annotated[Application, Depends(get_application)],
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for definition in application.catalog.all_jobs:
        runs = await application.run_repo.recent_for_job(definition.id, 1)
        result.append(
            {
                "definition": _definition_json(definition),
                "last_run": _run_json(runs[0]) if runs else None,
            }
        )
    return result


@router.get("/{job_id}")
async def job_detail(
    job_id: str, application: Annotated[Application, Depends(get_application)]
) -> JSONResponse:
    definition = application.catalog.get(JobId(job_id))
    if definition is None:
        return JSONResponse({"detail": "unknown job"}, status_code=404)
    runs = await application.run_repo.recent_for_job(JobId(job_id), 10)
    return JSONResponse(
        {"definition": _definition_json(definition), "runs": [_run_json(run) for run in runs]}
    )


@router.post("/{job_id}/run", status_code=202)
async def run_now(
    job_id: str, application: Annotated[Application, Depends(get_application)]
) -> JSONResponse:
    definition = application.catalog.get(JobId(job_id))
    if definition is None:
        return JSONResponse({"detail": "unknown job"}, status_code=404)
    task = asyncio.create_task(
        application.execute_job.execute(definition, scheduled_for=None),
        name=f"manual:{job_id}",
    )
    _manual_runs.add(task)
    task.add_done_callback(_manual_runs.discard)
    logger.info("manual run requested for %s", job_id)
    return JSONResponse({"status": "started", "job_id": job_id}, status_code=202)
