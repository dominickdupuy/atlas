"""/api/status and the passive board it feeds.

Probes are replaced with fakes throughout: what the board shows when Home
Assistant is down must not depend on whether anything happens to be
listening on the machine running the suite.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from httpx import AsyncClient

from atlas.bootstrap.container import Application
from atlas.jobs.domain.run import JobRun, RunState
from atlas.presentation.http.status import (
    MAX_ALERTS,
    MAX_RUNS,
    RECURRING_THRESHOLD,
    TIMELINE_DAYS,
)
from atlas.shared.ids import JobId, RunId
from atlas.telemetry.infrastructure.service_probes import TcpServiceProbe
from tests.integration.conftest import AUTH


class _FakeWriter:
    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


async def _accept(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return cast(asyncio.StreamReader, object()), cast(asyncio.StreamWriter, _FakeWriter())


async def _refuse(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    raise ConnectionRefusedError("[Errno 111] Connect call failed")


def _probes(*, homeassistant_up: bool, mosquitto_up: bool) -> tuple[TcpServiceProbe, ...]:
    return (
        TcpServiceProbe(
            "homeassistant", "127.0.0.1", 8123, connect=_accept if homeassistant_up else _refuse
        ),
        TcpServiceProbe(
            "mosquitto", "127.0.0.1", 1883, connect=_accept if mosquitto_up else _refuse
        ),
    )


@pytest.fixture(autouse=True)
def _healthy_probes(application: Application) -> None:
    application.probes = _probes(homeassistant_up=True, mosquitto_up=True)


async def _seed_run(
    application: Application,
    *,
    run_id: str,
    state: RunState,
    error: str | None = None,
    job_id: str = "lights-out",
) -> None:
    finished = datetime.now(UTC)
    await application.run_repo.add(
        JobRun(
            run_id=RunId(run_id),
            job_id=JobId(job_id),
            tier=1,
            mode="propose",
            state=state,
            started_at=finished - timedelta(seconds=3),
            finished_at=finished,
            error=error,
        )
    )


async def test_status_reports_every_section_the_board_draws(client: AsyncClient) -> None:
    response = await client.get("/api/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    for key in (
        "generated_at",
        "service",
        "modes",
        "alerts",
        "approvals",
        "runs",
        "containers",
        "system",
        "budget",
    ):
        assert key in body, key
    assert body["service"]["jobs_total"] == 1
    assert [container["name"] for container in body["containers"]] == [
        "homeassistant",
        "mosquitto",
    ]


async def test_status_requires_the_token(client: AsyncClient) -> None:
    assert (await client.get("/api/status")).status_code == 401


async def test_a_failed_run_becomes_a_critical_alert(
    client: AsyncClient, application: Application
) -> None:
    await _seed_run(application, run_id="run-1", state=RunState.FAILED, error="tool exploded")

    body = (await client.get("/api/status", headers=AUTH)).json()

    failures = [alert for alert in body["alerts"] if alert["kind"] == "job_failed"]
    assert len(failures) == 1
    assert failures[0]["severity"] == "critical"
    assert "lights-out" in failures[0]["summary"]
    assert failures[0]["detail"] == "tool exploded"


async def test_a_timed_out_run_also_alerts(client: AsyncClient, application: Application) -> None:
    await _seed_run(application, run_id="run-2", state=RunState.TIMED_OUT)

    body = (await client.get("/api/status", headers=AUTH)).json()

    assert any(alert["kind"] == "job_failed" for alert in body["alerts"])


async def test_a_completed_run_does_not_alert(
    client: AsyncClient, application: Application
) -> None:
    await _seed_run(application, run_id="run-3", state=RunState.COMPLETED)

    body = (await client.get("/api/status", headers=AUTH)).json()

    assert body["alerts"] == []
    assert body["runs"][0]["state"] == "completed"
    assert body["runs"][0]["duration_seconds"] == pytest.approx(3.0, abs=0.5)


async def test_a_down_container_is_a_critical_alert(
    client: AsyncClient, application: Application
) -> None:
    application.probes = _probes(homeassistant_up=False, mosquitto_up=True)

    body = (await client.get("/api/status", headers=AUTH)).json()

    down = [alert for alert in body["alerts"] if alert["kind"] == "container_down"]
    assert len(down) == 1
    assert down[0]["summary"] == "homeassistant unreachable"
    containers = {c["name"]: c for c in body["containers"]}
    assert containers["homeassistant"]["reachable"] is False
    assert containers["mosquitto"]["reachable"] is True


async def test_container_alerts_sort_above_job_failures(
    client: AsyncClient, application: Application
) -> None:
    """Priority order is the panel's whole design: what is broken now beats
    what broke earlier."""
    await _seed_run(application, run_id="run-4", state=RunState.FAILED, error="x")
    application.probes = _probes(homeassistant_up=False, mosquitto_up=False)

    body = (await client.get("/api/status", headers=AUTH)).json()

    kinds = [alert["kind"] for alert in body["alerts"]]
    assert kinds.index("container_down") < kinds.index("job_failed")


async def test_alerts_truncate_with_an_honest_total(
    client: AsyncClient, application: Application
) -> None:
    """D11 forbids scrolling, so overflow must be counted, not dropped."""
    for index in range(MAX_ALERTS + 4):
        await _seed_run(application, run_id=f"run-many-{index}", state=RunState.FAILED, error="e")

    body = (await client.get("/api/status", headers=AUTH)).json()

    assert len(body["alerts"]) == MAX_ALERTS
    assert body["alerts_total"] == MAX_ALERTS + 4


async def test_runs_are_capped_for_the_screen(
    client: AsyncClient, application: Application
) -> None:
    for index in range(MAX_RUNS + 3):
        await _seed_run(application, run_id=f"run-cap-{index}", state=RunState.COMPLETED)

    body = (await client.get("/api/status", headers=AUTH)).json()

    assert len(body["runs"]) == MAX_RUNS


async def test_authority_reports_no_unattended_writes_for_the_fixture_job(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/status", headers=AUTH)).json()

    assert body["modes"]["write_capable"] == []
    assert body["modes"]["counts"]["propose"] == 1


async def test_system_metrics_are_present(client: AsyncClient) -> None:
    body = (await client.get("/api/status", headers=AUTH)).json()

    system = body["system"]
    assert set(system) >= {
        "cpu_temp_c",
        "load_1",
        "mem_used_percent",
        "disk_used_percent",
        "uptime_seconds",
        "wifi",
    }
    assert system["disk_used_percent"] is not None


async def test_board_is_served_and_carries_no_input_controls(client: AsyncClient) -> None:
    response = await client.get("/dashboard", headers=AUTH)

    assert response.status_code == 200
    page = response.text
    assert "/static/board.css" in page
    assert "/static/board.js" in page
    assert "panel-timeline" in page
    assert 'id="attention"' in page
    assert 'id="stale"' in page
    # D11: the monitor has no input device, so the board must not offer any.
    for control in ("<button", "<form", "<input", "<a "):
        assert control not in page, f"the passive board must not contain {control}"


async def test_board_needs_no_data_to_render(client: AsyncClient) -> None:
    """The shell must paint before the first poll returns, so that a board
    that cannot reach the API still shows its own stale-data warning."""
    response = await client.get("/dashboard", headers=AUTH)

    assert 'id="stale"' in response.text
    assert "NO DATA" not in response.text, "the warning text is set by the client, not baked in"


# --- timeline, calendar and weather ----------------------------------------


async def _seed_run_at(
    application: Application,
    *,
    run_id: str,
    hour: int,
    minute: int = 0,
    state: RunState = RunState.COMPLETED,
    job_id: str = "lights-out",
    error: str | None = None,
) -> None:
    """Pinned to a mid-day hour so the run lands inside the board's band and
    on today's column regardless of when the suite runs."""
    started = datetime.now(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    await application.run_repo.add(
        JobRun(
            run_id=RunId(run_id),
            job_id=JobId(job_id),
            tier=1,
            mode="propose",
            state=state,
            started_at=started,
            finished_at=started + timedelta(seconds=2),
            error=error,
        )
    )


async def test_timeline_covers_three_days_starting_today(client: AsyncClient) -> None:
    body = (await client.get("/api/status", headers=AUTH)).json()

    days = body["timeline_days"]
    assert len(days) == TIMELINE_DAYS
    assert days[0]["is_today"] is True
    assert [day["day_offset"] for day in days] == list(range(TIMELINE_DAYS))
    assert body["timeline_start_hour"] < body["timeline_end_hour"]


async def test_repetitive_successes_collapse_into_one_band(
    client: AsyncClient, application: Application
) -> None:
    """A */30 job is thirty green bars a day; collapsing them is what keeps
    the one red bar visible (D11)."""
    for index in range(RECURRING_THRESHOLD + 3):
        await _seed_run_at(application, run_id=f"ok-{index}", hour=9, minute=index * 5)

    body = (await client.get("/api/status", headers=AUTH)).json()

    runs = [e for e in body["timeline"] if e["kind"] == "job_run"]
    assert len(runs) == 1, "repetitive successes must not each get a block"
    assert runs[0]["count"] == RECURRING_THRESHOLD + 3
    assert "all completed" in runs[0]["detail"]
    assert runs[0]["end_minutes"] > runs[0]["start_minutes"], "the band spans first to last"


async def test_failures_are_never_collapsed_away(
    client: AsyncClient, application: Application
) -> None:
    """The whole point of grouping is to keep this one visible."""
    for index in range(RECURRING_THRESHOLD + 3):
        await _seed_run_at(application, run_id=f"ok-{index}", hour=9, minute=index * 5)
    await _seed_run_at(
        application, run_id="bad", hour=10, state=RunState.FAILED, error="upstream 502"
    )

    body = (await client.get("/api/status", headers=AUTH)).json()

    failures = [e for e in body["timeline"] if e.get("status") == "failed"]
    assert len(failures) == 1
    assert failures[0]["count"] == 1
    assert failures[0]["label"] == "lights-out"


async def test_a_handful_of_runs_stay_individual(
    client: AsyncClient, application: Application
) -> None:
    for index in range(RECURRING_THRESHOLD):
        await _seed_run_at(application, run_id=f"few-{index}", hour=9, minute=index * 5)

    body = (await client.get("/api/status", headers=AUTH)).json()

    runs = [e for e in body["timeline"] if e["kind"] == "job_run"]
    assert len(runs) == RECURRING_THRESHOLD
    assert all(entry["count"] == 1 for entry in runs)


async def test_calendar_reports_that_it_is_not_configured(client: AsyncClient) -> None:
    """With no feed URL set, the board says so rather than drawing plausible
    meetings."""
    body = (await client.get("/api/status", headers=AUTH)).json()

    calendar = body["calendar"]
    assert calendar["configured"] is False
    assert "ATLAS_CALENDAR_ICS_URL" in calendar["detail"]
    assert calendar["event_count"] == 0
    assert calendar["synced_at"] is None
    assert not [e for e in body["timeline"] if e["kind"] == "calendar_event"]


async def test_weather_never_reports_stub_numbers_as_real(client: AsyncClient) -> None:
    """StubWeather returns a canned 21C. On a wall display that is a lie, so
    the dev profile must report unavailable rather than pass it through."""
    body = (await client.get("/api/status", headers=AUTH)).json()

    weather = body["weather"]
    assert weather["available"] is False
    assert weather["temperature_c"] is None
    assert weather["summary"] is None
    assert "stub" in (weather["detail"] or "")
