"""ToolGateway: the allowlist and ceiling enforcement point."""

from __future__ import annotations

import pytest

from pihome.connectors.application.gateway import (
    ToolCallBudgetExceeded,
    ToolGateway,
    ToolNotPermitted,
    UnknownToolServer,
)
from pihome.connectors.domain.tools import ToolAllowlist, ToolCall
from pihome.connectors.infrastructure.stubs import StubMcpClient, StubWeather


def _gateway(tools: set[str], max_calls: int = 10) -> ToolGateway:
    return ToolGateway(
        allowlist=ToolAllowlist(tools=frozenset(tools)),
        clients={"google-calendar": StubMcpClient(), "home-assistant": StubMcpClient()},
        weather=StubWeather(),
        max_tool_calls=max_calls,
    )


async def test_allowlisted_call_routes_to_the_client() -> None:
    gateway = _gateway({"google-calendar.list_events"})
    result = await gateway.call(ToolCall(tool="google-calendar.list_events", args={}))
    assert not result.is_error
    assert gateway.calls_made == 1


async def test_non_allowlisted_call_is_refused() -> None:
    gateway = _gateway({"google-calendar.list_events"})
    with pytest.raises(ToolNotPermitted):
        await gateway.call(ToolCall(tool="home-assistant.turn_off", args={}))
    assert gateway.calls_made == 0


async def test_ceiling_stops_further_calls() -> None:
    gateway = _gateway({"google-calendar.list_events"}, max_calls=2)
    call = ToolCall(tool="google-calendar.list_events", args={})
    await gateway.call(call)
    await gateway.call(call)
    with pytest.raises(ToolCallBudgetExceeded):
        await gateway.call(call)


async def test_unknown_server_raises() -> None:
    gateway = _gateway({"nonexistent.tool"})
    with pytest.raises(UnknownToolServer):
        await gateway.call(ToolCall(tool="nonexistent.tool", args={}))


async def test_weather_routes_in_process() -> None:
    gateway = _gateway({"weather.get_forecast"})
    result = await gateway.call(
        ToolCall(tool="weather.get_forecast", args={"latitude": 40.7, "longitude": -74.0})
    )
    assert not result.is_error
    assert isinstance(result.content, dict)
    assert result.content["summary"] == "partly cloudy"


async def test_weather_requires_coordinates() -> None:
    gateway = _gateway({"weather.get_forecast"})
    result = await gateway.call(ToolCall(tool="weather.get_forecast", args={}))
    assert result.is_error
