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
from atlas.presentation.http.runs import MAX_PENDING
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


async def test_status_includes_hosted_queue_and_history_in_stub_profile(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps({"status": "ok", "started": "2026-09-05T16:52:20", "exit": 0}),
        encoding="utf-8",
    )
    (state / "queue.json").write_text(
        json.dumps([{"name": "finance", "at": "2099-01-01T08:30", "note": "one-off"}]),
        encoding="utf-8",
    )

    body = (await client.get("/api/status", headers=AUTH)).json()
    timeline = body["run_timeline"]
    repo_pending = [row for row in timeline["pending"] if row["origin"] == "repo"]

    # A cron fire carries no detail: every hosted repo fires from cron, so the
    # word would only cost width (runs._SILENT_TRIGGER). One-offs still speak.
    assert {row["detail"] for row in repo_pending} == {"", "one-off"}
    assert all(row["state"] == "queued" for row in repo_pending)
    assert timeline["pending_total"] == 3  # cron, the queued one-off, and the Atlas job
    assert timeline["history"][0]["name"] == "finance"
    assert timeline["history"][0]["state"] == "completed"


async def test_status_rereads_the_queue_on_each_poll(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    queue = state / "queue.json"
    queue.write_text(
        json.dumps([{"name": "finance", "at": "2099-01-01T08:30", "note": "newly queued"}]),
        encoding="utf-8",
    )
    first = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]
    queue.write_text("[]", encoding="utf-8")
    second = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert any(row["detail"] == "newly queued" for row in first["pending"])
    assert second["pending_total"] == first["pending_total"] - 1
    assert all(row["detail"] != "newly queued" for row in second["pending"])


async def test_running_hosted_repo_is_pending_in_both_boards(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps({"status": "running", "started": "2026-09-05T16:52:20", "trigger": "cron"}),
        encoding="utf-8",
    )
    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]
    panel = (await client.get("/partials/jobs", headers=AUTH)).text

    assert timeline["pending"][0]["state"] == "running"
    assert timeline["pending"][0]["name"] == "finance"
    assert timeline["history"] == []
    assert "badge-running" in panel
    assert "badge-failed" not in panel


async def test_status_reports_total_before_queue_truncation(
    client: AsyncClient, application: Application
) -> None:
    state = _register_repo(application)
    (state / "queue.json").write_text(
        json.dumps(
            [
                {"name": "finance", "at": "2099-01-01T08:30", "note": str(n)}
                for n in range(MAX_PENDING + 4)
            ]
        ),
        encoding="utf-8",
    )
    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert len(timeline["pending"]) == MAX_PENDING
    # every queued entry, plus the repo's cron fire and the Atlas job's
    assert timeline["pending_total"] == MAX_PENDING + 6


async def test_status_separates_pending_atlas_work_from_finished_history(
    client: AsyncClient, application: Application
) -> None:
    application.settings.profile = "prod"
    await _seed_run(application, RunState.AWAITING_APPROVAL, "waiting")
    await _seed_run(application, RunState.COMPLETED, "finished")

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert [row["state"] for row in timeline["pending"]] == ["awaiting_approval", "queued"]
    assert [row["state"] for row in timeline["history"]] == ["completed"]


async def test_stub_atlas_runs_reach_the_board_carrying_their_label(
    client: AsyncClient, application: Application
) -> None:
    """The dev profile used to empty the panel of Atlas work entirely, so the
    screen read "idle" while the scheduler fired all day. Show the work; say
    what it is."""
    await _seed_run(application, RunState.COMPLETED)

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]
    atlas = [row for row in timeline["history"] if row["origin"] == "atlas"]

    assert [row["name"] for row in atlas] == ["lights-out"]
    assert atlas[0]["detail"] == "stub"


async def test_a_repeating_job_collapses_instead_of_filling_the_panel(
    client: AsyncClient, application: Application
) -> None:
    """calendar-today fires every half hour. Left as one row each it evicts
    every other origin from an eight-row panel, so the panel stops being the
    combined timeline it exists to be."""
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps({"status": "ok", "started": "2020-01-01T16:52:20", "exit": 0}),
        encoding="utf-8",
    )
    for index in range(12):
        await _seed_run(application, RunState.COMPLETED, f"repeat-{index}")

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert [row["name"] for row in timeline["history"]] == ["lights-out", "finance"]
    assert "12 runs" in timeline["history"][0]["detail"]
    assert timeline["history_total"] == 2


async def test_a_failure_is_not_collapsed_into_the_successes_around_it(
    client: AsyncClient, application: Application
) -> None:
    await _seed_run(application, RunState.COMPLETED, "before")
    await _seed_run(application, RunState.FAILED, "boom")
    await _seed_run(application, RunState.COMPLETED, "after")

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert [row["state"] for row in timeline["history"]] == ["completed", "failed", "completed"]


async def test_runs_of_a_retired_job_leave_the_board_with_it(
    client: AsyncClient, application: Application
) -> None:
    """Removing a job definition is how a person says "stop": its old rows in
    SQLite must not keep it on the screen."""
    now = datetime.now(UTC)
    await application.run_repo.add(
        JobRun(
            run_id=RunId("ghost"),
            job_id=JobId("retired-job"),
            tier=1,
            mode="read",
            state=RunState.COMPLETED,
            started_at=now - timedelta(seconds=3),
            finished_at=now,
        )
    )
    await _seed_run(application, RunState.COMPLETED)

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]

    assert [row["name"] for row in timeline["history"]] == ["lights-out"]


async def test_finance_figures_ride_on_the_run_detail(
    client: AsyncClient, application: Application
) -> None:
    """The point of the finance row: how many transactions the ledger holds
    and how many are still waiting for a category, readable at a glance."""
    state = _register_repo(application)
    (state / "finance.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "started": "2026-09-05T16:52:20",
                "trigger": "cron",
                "duration_seconds": 57.2,
                "exit": 0,
                "summary": {
                    "transactions": 1204,
                    "reviewed": 3,
                    "uncategorized": 0,
                    "decisions": 2,
                    "rules": 1,
                    "uncategorized_before": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    timeline = (await client.get("/api/status", headers=AUTH)).json()["run_timeline"]
    finance = next(row for row in timeline["history"] if row["name"] == "finance")

    assert finance["detail"] == (
        # The cron trigger is silent (runs._SILENT_TRIGGER); the figures lead.
        "57s · 1,204 transactions · 3 reviewed · 0 uncategorized · 2 decisions · 1 new rules"
    )


async def test_health_job_figures_reach_the_runs_panel(
    application: Application, client: AsyncClient
) -> None:
    """The nightly analysis reports its figures like any hosted repo."""
    state_dir = application.settings.repos_state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    application.settings.repos_registry.write_text(
        '[[repo]]\nname = "health-nightly"\npath = "/opt/health"\nkind = "job"\n',
        encoding="utf-8",
    )
    (state_dir / "health-nightly.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "trigger": "cron",
                "started": "2026-09-07T10:00:00",
                "duration_seconds": 12.0,
                "exit": 0,
                "summary": {"nights": 20, "alerts": 1, "runs": 8, "weigh_ins": 0},
            }
        ),
        encoding="utf-8",
    )
    response = await client.get("/api/status", headers=AUTH)
    history = response.json()["run_timeline"]["history"]
    entry = next(item for item in history if item["name"] == "health-nightly")
    assert "20 nights" in entry["detail"] and "1 alerts" in entry["detail"]
