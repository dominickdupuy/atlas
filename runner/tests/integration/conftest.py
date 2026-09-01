"""Integration fixtures: a full Application over a tmp database and the
fixture jobs directory, served through httpx's ASGI transport. Lifespan
background tasks (scheduler, MQTT, sweeps) are deliberately NOT started —
API behavior must not depend on them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from atlas.bootstrap.container import Application, build_application
from atlas.config import Settings
from atlas.connectors.application.ports import DayForecast, HourlyConditions, WeatherReport
from atlas.presentation.http.app import create_app

FIXTURE_JOBS = Path(__file__).parent.parent / "fixtures" / "jobs"

API_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


SAMPLE_WEATHER = WeatherReport(
    current_c=29.0,
    current_summary="overcast",
    fetched_at=datetime(2026, 9, 1, 20, 0, tzinfo=UTC),
    days=(
        DayForecast(
            date="2026-09-01",
            summary="rain",
            high_c=31.0,
            low_c=23.1,
            uv_index_max=6.9,
            precipitation_probability_pct=40,
            precipitation_mm=4.3,
            hourly=tuple(
                HourlyConditions(
                    time=datetime(2026, 9, 1, hour, tzinfo=UTC),
                    probability_pct=hour * 2,
                    millimetres=0.5 if hour > 12 else 0.0,
                    uv_index=max(0.0, 7.0 - abs(13 - hour)),
                )
                for hour in range(24)
            ),
        ),
        DayForecast(
            date="2026-09-02",
            summary="clear",
            high_c=28.8,
            low_c=23.8,
            uv_index_max=6.05,
            precipitation_probability_pct=5,
            precipitation_mm=0.0,
            hourly=tuple(
                HourlyConditions(
                    time=datetime(2026, 9, 2, hour, tzinfo=UTC),
                    probability_pct=2,
                    millimetres=0.0,
                    uv_index=max(0.0, 6.0 - abs(13 - hour)),
                )
                for hour in range(24)
            ),
        ),
    ),
)


class FakeWeatherReport:
    """The board's weather must never depend on Open-Meteo being reachable
    from wherever the suite runs."""

    def __init__(self, report: WeatherReport | None = None) -> None:
        self.report = report if report is not None else SAMPLE_WEATHER

    async def get_report(self, latitude: float, longitude: float) -> WeatherReport:
        return self.report


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
    app.weather_report = FakeWeatherReport()
    await app.start_persistence()
    yield app
    await app.db.close()


@pytest.fixture
async def client(application: Application) -> AsyncIterator[AsyncClient]:
    api = create_app(application)
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
