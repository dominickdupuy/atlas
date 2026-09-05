"""The Runs panel: queued work and history, atlas jobs and hosted repos.

Two systems put work on this Pi. The panel is the one place that shows both,
so these tests pin the ordering rules rather than just the presence of text:
what is still to happen sits above what already did, and a run blocked on a
person sits above a run merely scheduled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient

from atlas.bootstrap.container import Application
from atlas.jobs.domain.run import JobRun, RunState
from atlas.shared.ids import JobId, RunId
from tests.integration.conftest import AUTH

REGISTRY = """
[[repo]]
name = "finance"
path = "/opt/finance"
url = "git@example:acme/finance.git"
kind = "job"
schedule = "30 8 * * *"
job = "run-it"
"""


def _register_repo(application: Application) -> Path:
    registry = application.settings.repos_registry
    registry.write_text(REGISTRY, encoding="utf-8")
    state = application.settings.repos_state_dir
    state.mkdir(parents=True, exist_ok=True)
    return state


async def _seed_run(application: Application, state: RunState, run_id: str = "r1") -> None:
    now = datetime.now(UTC)
    await application.run_repo.add(
        JobRun(
            run_id=RunId(run_id),
            job_id=JobId("lights-out"),
            tier=1,
            mode="propose",
            state=state,
            started_at=now - timedelta(seconds=3),
            finished_at=None if state is RunState.AWAITING_APPROVAL else now,
        )
    )


async def test_scheduled_atlas_jobs_show_as_queued(client: AsyncClient) -> None:
    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "badge-queued" in panel
    assert "lights-out" in panel


async def test_a_hosted_repo_run_appears_beside_atlas_runs(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps(
            {
                "name": "finance",
                "trigger": "cron",
                "started": "2026-09-05T16:52:20",
                "status": "ok",
                "duration_seconds": 57.2,
                "exit": 0,
            }
        ),
        encoding="utf-8",
    )

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "finance" in panel
    assert "origin-repo" in panel
    assert "57s" in panel


async def test_a_failed_repo_run_reports_its_exit_code(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps(
            {
                "name": "finance",
                "trigger": "cron",
                "started": "2026-09-05T16:52:20",
                "status": "failed",
                "duration_seconds": 3.0,
                "exit": 2,
            }
        ),
        encoding="utf-8",
    )

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "badge-failed" in panel
    assert "exit 2" in panel


async def test_a_queued_one_off_is_labelled_with_its_note(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "queue.json").write_text(
        json.dumps([{"name": "finance", "at": "2099-01-01T08:30", "note": "after the key fix"}]),
        encoding="utf-8",
    )

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "after the key fix" in panel


async def test_awaiting_approval_sorts_above_merely_scheduled_work(
    client: AsyncClient, application: Application
) -> None:
    """A run blocked on a person is the one thing on this panel someone can
    act on right now; it must not sit below tomorrow's cron fire."""
    await _seed_run(application, RunState.AWAITING_APPROVAL)

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "waiting on approval" in panel
    assert panel.index("waiting on approval") < panel.index("badge-queued")


async def test_finished_runs_sit_below_pending_work(
    client: AsyncClient, application: Application
) -> None:
    await _seed_run(application, RunState.COMPLETED)

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert panel.index("badge-queued") < panel.index("badge-completed")


async def test_truncation_admits_how_much_was_hidden(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "queue.json").write_text(
        json.dumps(
            [{"name": "finance", "at": f"2099-01-0{n}T08:30", "note": ""} for n in range(1, 9)]
        ),
        encoding="utf-8",
    )

    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert "more queued" in panel
