"""The board: full page, partials, and the SSE stream."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from httpx import AsyncClient

from pihome.bootstrap.container import Application
from pihome.jobs.domain.events import JobRunStarted
from pihome.presentation.http.panels import PANEL_NAMES
from pihome.shared.ids import JobId, RunId
from tests.integration.conftest import AUTH


async def test_board_renders_every_panel(client: AsyncClient) -> None:
    response = await client.get("/", headers=AUTH)
    assert response.status_code == 200
    page = response.text
    for panel in PANEL_NAMES:
        if panel == "mode":
            continue
        assert f"panel-{panel}" in page
    assert "sse-connect" in page
    assert "lights-out" in page  # fixture job appears in the schedule


async def test_each_partial_renders(client: AsyncClient) -> None:
    for panel in PANEL_NAMES:
        response = await client.get(f"/partials/{panel}", headers=AUTH)
        assert response.status_code == 200, panel


async def test_unknown_partial_is_404(client: AsyncClient) -> None:
    assert (await client.get("/partials/nope", headers=AUTH)).status_code == 404


async def test_sse_emits_a_rendered_fragment_on_domain_event(
    application: Application,
) -> None:
    # httpx's ASGITransport buffers complete responses, so an endless SSE
    # stream must be driven at the ASGI level: run the app as a task, feed
    # it a request, and read body chunks as they are sent.
    import contextlib
    from collections.abc import MutableMapping
    from typing import Any

    from pihome.presentation.http.app import create_app

    api = create_app(application)
    chunks: asyncio.Queue[bytes] = asyncio.Queue()
    request_sent = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()  # never disconnect during the test
        raise AssertionError("unreachable")

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            assert isinstance(body, bytes)
            await chunks.put(body)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/events",
        "raw_path": b"/events",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"authorization", b"Bearer test-token"),
            (b"host", b"testserver"),
            (b"accept", b"text/event-stream"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    app_task = asyncio.create_task(api(scope, receive, send))
    try:
        await asyncio.sleep(0.2)  # let the stream subscribe to the bus
        await application.bus.publish(
            JobRunStarted(
                occurred_at=datetime.now(UTC),
                job_id=JobId("lights-out"),
                run_id=RunId("run-sse"),
                tier=1,
                mode="propose",
                attempt=1,
            )
        )
        buffer = b""
        async with asyncio.timeout(10):
            while b"event: jobs" not in buffer or b"<h2>Runs</h2>" not in buffer:
                buffer += await chunks.get()
    finally:
        app_task.cancel()
        with contextlib.suppress(BaseException):
            await app_task


async def test_jobs_api_lists_fixture_job(client: AsyncClient) -> None:
    response = await client.get("/api/jobs", headers=AUTH)
    assert response.status_code == 200
    jobs = response.json()
    assert [job["definition"]["id"] for job in jobs] == ["lights-out"]
    assert jobs[0]["last_run"] is None

    detail = await client.get("/api/jobs/lights-out", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["definition"]["mode"] == "propose"

    assert (await client.get("/api/jobs/nope", headers=AUTH)).status_code == 404
