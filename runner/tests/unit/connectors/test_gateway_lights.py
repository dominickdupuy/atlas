from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from atlas.connectors.application.gateway import ToolGateway
from atlas.connectors.domain.tools import ToolAllowlist, ToolCall
from atlas.connectors.infrastructure.stubs import StubWeather


class _FakeLights:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set_lights(
        self, targets: Sequence[str], command: dict[str, JsonValue], *, toggle: bool = False
    ) -> dict[str, JsonValue]:
        self.calls.append(("set", (list(targets), command, toggle)))
        return {"applied": list(targets), "failed": []}

    async def activate_scene(self, name: str) -> dict[str, JsonValue]:
        self.calls.append(("scene", name))
        return {"scene": name, "applied": [], "failed": []}

    def state(self) -> dict[str, JsonValue]:
        return {"lights": {}}


def _gateway(lights: _FakeLights | None) -> ToolGateway:
    return ToolGateway(
        allowlist=ToolAllowlist(
            tools=frozenset({"lights.set", "lights.scene", "lights.state", "lights.explode"})
        ),
        clients={},
        weather=StubWeather(),
        max_tool_calls=8,
        lights=lights,
    )


async def test_set_routes_in_process_with_command_fields_split_out() -> None:
    lights = _FakeLights()
    result = await _gateway(lights).call(
        ToolCall(
            tool="lights.set", args={"targets": ["bedroom"], "brightness": 40, "toggle": False}
        )
    )
    assert not result.is_error
    assert lights.calls == [("set", (["bedroom"], {"brightness": 40}, False))]


async def test_scene_and_state() -> None:
    lights = _FakeLights()
    gateway = _gateway(lights)
    assert (
        await gateway.call(ToolCall(tool="lights.scene", args={"name": "evening"}))
    ).content == {"scene": "evening", "applied": [], "failed": []}
    assert (await gateway.call(ToolCall(tool="lights.state"))).content == {"lights": {}}


async def test_unconfigured_and_unknown_tool_are_error_results() -> None:
    none = await _gateway(None).call(ToolCall(tool="lights.state"))
    assert none.is_error
    unknown = await _gateway(_FakeLights()).call(ToolCall(tool="lights.explode"))
    assert unknown.is_error


async def test_missing_targets_is_an_error_result() -> None:
    result = await _gateway(_FakeLights()).call(ToolCall(tool="lights.set", args={"on": True}))
    assert result.is_error
