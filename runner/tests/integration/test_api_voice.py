"""/api/voice (spec 7.1, D29). The echo contract is what the Shortcut is
built against; it must keep working when nothing behind it is wired."""

from __future__ import annotations

from httpx import AsyncClient

from atlas.bootstrap.container import Application
from tests.integration.conftest import AUTH


async def test_requires_auth(client: AsyncClient) -> None:
    assert (await client.post("/api/voice", json={"text": "hi"})).status_code == 401


async def test_echo_contract(client: AsyncClient, application: Application) -> None:
    application.voice = None
    response = await client.post("/api/voice", headers=AUTH, json={"text": "lights off"})
    assert response.status_code == 200
    assert response.json() == {
        "speech": "Heard: lights off",
        "intent": None,
        "tier": 0,
        "result": None,
    }


async def test_empty_and_oversized_text_are_422(client: AsyncClient) -> None:
    assert (await client.post("/api/voice", headers=AUTH, json={"text": ""})).status_code == 422
    assert (
        await client.post("/api/voice", headers=AUTH, json={"text": "x" * 501})
    ).status_code == 422
    assert (await client.post("/api/voice", headers=AUTH, json={})).status_code == 422


async def test_real_service_sets_lights_over_http(client: AsyncClient) -> None:
    response = await client.post(
        "/api/voice", headers=AUTH, json={"text": "bedroom lights to 40 percent"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == 1
    assert body["speech"] == "Bedroom at 40 percent."
    assert body["intent"]["intent"] == "set_light"
    assert body["result"]["failed"] == []
    state = await client.get("/api/lights/ceiling-2", headers=AUTH)
    assert state.json()["brightness"] == 40
