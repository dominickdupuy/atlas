"""/api/voice (spec 7.1, D29). The echo contract is what the Shortcut is
built against; it must keep working when nothing behind it is wired."""

from __future__ import annotations

from httpx import AsyncClient

from tests.integration.conftest import AUTH


async def test_requires_auth(client: AsyncClient) -> None:
    assert (await client.post("/api/voice", json={"text": "hi"})).status_code == 401


async def test_echo_contract(client: AsyncClient, application: object) -> None:
    application.voice = None  # type: ignore[attr-defined]
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
