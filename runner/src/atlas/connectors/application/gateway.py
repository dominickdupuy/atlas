"""ToolGateway: the only way any code invokes a tool.

Enforces the per-job allowlist (spec §7) and the tool-call ceiling on every
call, and routes by server prefix: `weather.*` to the in-process HTTP
connector (D19), everything else to the matching MCP client. One gateway is
built per run so the call counter is fresh.
"""

from __future__ import annotations

from atlas.connectors.application.ports import McpClient, WeatherPort
from atlas.connectors.domain.tools import (
    WEATHER_SERVER,
    ToolAllowlist,
    ToolCall,
    ToolResult,
)


class ToolNotPermitted(Exception):
    def __init__(self, tool: str) -> None:
        super().__init__(f"tool {tool!r} is not in this job's allowlist")
        self.tool = tool


class ToolCallBudgetExceeded(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"tool call ceiling reached ({limit})")
        self.limit = limit


class UnknownToolServer(Exception):
    def __init__(self, server: str) -> None:
        super().__init__(f"no connector configured for tool server {server!r}")
        self.server = server


class ToolGateway:
    def __init__(
        self,
        *,
        allowlist: ToolAllowlist,
        clients: dict[str, McpClient],
        weather: WeatherPort,
        max_tool_calls: int,
    ) -> None:
        self._allowlist = allowlist
        self._clients = clients
        self._weather = weather
        self._max_tool_calls = max_tool_calls
        self._calls = 0

    @property
    def calls_made(self) -> int:
        return self._calls

    async def call(self, call: ToolCall) -> ToolResult:
        if not self._allowlist.permits(call.tool):
            raise ToolNotPermitted(call.tool)
        if self._calls >= self._max_tool_calls:
            raise ToolCallBudgetExceeded(self._max_tool_calls)
        self._calls += 1

        if call.server == WEATHER_SERVER:
            return await self._call_weather(call)
        client = self._clients.get(call.server)
        if client is None:
            raise UnknownToolServer(call.server)
        return await client.call_tool(call)

    async def _call_weather(self, call: ToolCall) -> ToolResult:
        if call.name != "get_forecast":
            return ToolResult(
                tool=call.tool, content=f"unknown weather tool {call.name!r}", is_error=True
            )
        latitude = call.args.get("latitude")
        longitude = call.args.get("longitude")
        if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
            return ToolResult(
                tool=call.tool, content="latitude and longitude are required", is_error=True
            )
        forecast = await self._weather.get_forecast(float(latitude), float(longitude))
        return ToolResult(tool=call.tool, content=forecast.model_dump())
