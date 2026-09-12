"""/api/lights over the stub controller (spec 5). Every status code."""

from __future__ import annotations

from httpx import AsyncClient

from atlas.bootstrap.container import Application
from tests.integration.conftest import AUTH


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
