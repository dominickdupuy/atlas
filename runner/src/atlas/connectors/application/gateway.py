"""ToolGateway: the only way any code invokes a tool.

Enforces the per-job allowlist (spec §7) and the tool-call ceiling on every
call, and routes by server prefix: `weather.*` to the in-process HTTP
connector (D19), everything else to the matching MCP client. One gateway is
built per run so the call counter is fresh.
"""

from __future__ import annotations

from atlas.connectors.application.ports import LightsToolPort, McpClient, WeatherPort
from atlas.connectors.domain.tools import (
    LIGHTS_SERVER,
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
        lights: LightsToolPort | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._clients = clients
        self._weather = weather
        self._max_tool_calls = max_tool_calls
        self._lights = lights
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
        if call.server == LIGHTS_SERVER:
            return await self._call_lights(call)
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

    _LIGHT_COMMAND_KEYS = ("on", "brightness", "color_temp_k", "hue", "saturation", "transition_ms")

    async def _call_lights(self, call: ToolCall) -> ToolResult:
        if self._lights is None:
            return ToolResult(tool=call.tool, content="lights are not configured", is_error=True)
        try:
            match call.name:
                case "set":
                    raw_targets = call.args.get("targets")
                    if not isinstance(raw_targets, list) or not all(
                        isinstance(t, str) for t in raw_targets
                    ):
                        return ToolResult(
                            tool=call.tool,
                            content="targets: list[str] is required",
                            is_error=True,
                        )
                    targets = [t for t in raw_targets if isinstance(t, str)]
                    command = {k: v for k, v in call.args.items() if k in self._LIGHT_COMMAND_KEYS}
                    toggle = bool(call.args.get("toggle", False))
                    content = await self._lights.set_lights(targets, command, toggle=toggle)
                case "scene":
                    name = call.args.get("name")
                    if not isinstance(name, str):
                        return ToolResult(tool=call.tool, content="name is required", is_error=True)
                    content = await self._lights.activate_scene(name)
                case "state":
                    content = self._lights.state()
                case _:
                    return ToolResult(
                        tool=call.tool,
                        content=f"unknown lights tool {call.name!r}",
                        is_error=True,
                    )
        except Exception as exc:  # the gateway never raises for a tool's own failure
            return ToolResult(tool=call.tool, content=str(exc), is_error=True)
        return ToolResult(tool=call.tool, content=content)
