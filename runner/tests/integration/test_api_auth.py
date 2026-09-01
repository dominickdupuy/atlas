"""The three ways through the auth middleware, and the exemptions."""

from __future__ import annotations

from httpx import AsyncClient

from tests.integration.conftest import API_TOKEN, AUTH


async def test_no_credentials_is_401(client: AsyncClient) -> None:
    assert (await client.get("/")).status_code == 401
    assert (await client.get("/api/approvals")).status_code == 401


async def test_wrong_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_bearer_header_passes(client: AsyncClient) -> None:
    assert (await client.get("/", headers=AUTH)).status_code == 200


async def test_healthz_is_exempt(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_static_is_exempt(client: AsyncClient) -> None:
    assert (await client.get("/static/dashboard.css")).status_code == 200


async def test_query_token_sets_cookie_and_redirects(client: AsyncClient) -> None:
    response = await client.get(f"/?token={API_TOKEN}")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "atlas_token" in response.headers.get("set-cookie", "")

    # The cookie now authenticates by itself (the kiosk's steady state).
    client.cookies.set("atlas_token", API_TOKEN)
    followup = await client.get("/")
    assert followup.status_code == 200


async def test_wrong_query_token_is_401(client: AsyncClient) -> None:
    assert (await client.get("/?token=wrong")).status_code == 401
