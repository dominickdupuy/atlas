"""/api/lights over the stub controller (spec 5). Every status code."""

from __future__ import annotations

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from atlas.bootstrap.container import Application, build_application
from atlas.config import Settings
from atlas.presentation.http.app import create_app
from tests.integration.conftest import AUTH, FIXTURE_JOBS


async def test_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/lights")).status_code == 401


async def test_list_and_get(client: AsyncClient) -> None:
    response = await client.get("/api/lights", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert set(body["lights"]) == {"ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4"}
    assert body["error"] is None
    assert body["fetched_at"] is not None

    one = await client.get("/api/lights/ceiling-1", headers=AUTH)
    assert one.status_code == 200
    assert one.json()["on"] is False

    assert (await client.get("/api/lights/kitchen", headers=AUTH)).status_code == 404


async def test_apply_returns_confirmed_state(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lights/ceiling-2",
        headers=AUTH,
        json={"on": True, "brightness": 60, "color_temp_k": 2700, "transition_ms": 500},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["on"] is True
    assert body["brightness"] == 60
    assert body["color_temp_k"] == 2703


async def test_apply_hue_and_saturation(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lights/ceiling-2", headers=AUTH, json={"hue": 120, "saturation": 100}
    )
    assert response.status_code == 200
    assert response.json()["hue"] == 120


async def test_bad_command_is_422(client: AsyncClient) -> None:
    bad = await client.post("/api/lights/ceiling-1", headers=AUTH, json={"brightness": 500})
    assert bad.status_code == 422
    exclusive = await client.post(
        "/api/lights/ceiling-1",
        headers=AUTH,
        json={"color_temp_k": 2700, "hue": 1, "saturation": 1},
    )
    assert exclusive.status_code == 422
    empty = await client.post("/api/lights/ceiling-1", headers=AUTH, json={})
    assert empty.status_code == 422


async def test_toggle(client: AsyncClient) -> None:
    first = await client.post("/api/lights/ceiling-3/toggle", headers=AUTH)
    assert first.status_code == 200 and first.json()["on"] is True
    second = await client.post("/api/lights/ceiling-3/toggle", headers=AUTH)
    assert second.json()["on"] is False


async def test_scenes_and_activate(client: AsyncClient) -> None:
    scenes = await client.get("/api/lights/scenes", headers=AUTH)
    assert scenes.status_code == 200
    assert {s["name"] for s in scenes.json()} == {"evening", "night", "off"}

    result = await client.post("/api/lights/scenes/evening/activate", headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["failed"] == []
    assert sorted(body["applied"]) == ["ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4"]
    assert body["snapshot"]["lights"]["ceiling-4"]["brightness"] == 40

    assert (await client.post("/api/lights/scenes/party/activate", headers=AUTH)).status_code == 404


async def test_controller_down_is_503_on_write_and_error_on_read(
    client: AsyncClient, application: Application
) -> None:
    assert application.lights is not None
    await application.lights.on_disconnected()
    write = await client.post("/api/lights/ceiling-1", headers=AUTH, json={"on": True})
    assert write.status_code == 503
    read = await client.get("/api/lights", headers=AUTH)
    assert read.status_code == 200
    assert read.json()["error"] == "controller unavailable"


async def test_unsupported_feature_is_409(client: AsyncClient, application: Application) -> None:
    assert application.lights is not None
    entry = application.lights._entries["ceiling-1"]
    entry.capabilities = entry.capabilities.model_copy(  # type: ignore[union-attr]
        update={"features": entry.capabilities.features.model_copy(update={"dimming": False})}  # type: ignore[union-attr]
    )
    response = await client.post("/api/lights/ceiling-1", headers=AUTH, json={"brightness": 10})
    assert response.status_code == 409


async def test_lights_not_configured_is_404_on_every_route(tmp_path: Path) -> None:
    """`ATLAS_MATTER_WS_URL` unset means `Application.lights is None`; every
    route must 404 rather than 500, independent of the stub fixture above."""
    token = "not-configured-token"
    settings = Settings(
        _env_file=None,
        profile="dev",
        api_token=token,
        db_path=tmp_path / "state.db",
        jobs_dir=FIXTURE_JOBS,
        tz="UTC",
        ntfy_token="",
        repos_registry=tmp_path / "repos.toml",
        repos_state_dir=tmp_path / "repo-state",
        health_board_path=tmp_path / "health-board.json",
        matter_ws_url="",
    )
    application = build_application(settings)
    await application.start_persistence()
    assert application.lights is None
    api = create_app(application)
    transport = ASGITransport(app=api)
    auth = {"Authorization": f"Bearer {token}"}
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            expected = {"detail": "lights are not configured"}
            get_lights = await client.get("/api/lights", headers=auth)
            assert get_lights.status_code == 404
            assert get_lights.json() == expected

            get_scenes = await client.get("/api/lights/scenes", headers=auth)
            assert get_scenes.status_code == 404
            assert get_scenes.json() == expected

            activate = await client.post("/api/lights/scenes/evening/activate", headers=auth)
            assert activate.status_code == 404
            assert activate.json() == expected

            get_one = await client.get("/api/lights/ceiling-1", headers=auth)
            assert get_one.status_code == 404
            assert get_one.json() == expected

            post_one = await client.post("/api/lights/ceiling-1", headers=auth, json={"on": True})
            assert post_one.status_code == 404
            assert post_one.json() == expected

            toggle = await client.post("/api/lights/ceiling-1/toggle", headers=auth)
            assert toggle.status_code == 404
            assert toggle.json() == expected
    finally:
        await application.db.close()
