"""Integration fixtures: a full Application over a tmp database and the
fixture jobs directory, served through httpx's ASGI transport. Lifespan
background tasks (scheduler, MQTT, sweeps) are deliberately NOT started —
API behavior must not depend on them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pihome.bootstrap.container import Application, build_application
from pihome.config import Settings
from pihome.presentation.http.app import create_app

FIXTURE_JOBS = Path(__file__).parent.parent / "fixtures" / "jobs"

API_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


@pytest.fixture
async def application(tmp_path: Path) -> AsyncIterator[Application]:
    settings = Settings(
        _env_file=None,
        profile="dev",
        api_token=API_TOKEN,
        db_path=tmp_path / "state.db",
        jobs_dir=FIXTURE_JOBS,
        tz="UTC",
        ntfy_token="",
    )
    app = build_application(settings)
    await app.start_persistence()
    yield app
    await app.db.close()


@pytest.fixture
async def client(application: Application) -> AsyncIterator[AsyncClient]:
    api = create_app(application)
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
