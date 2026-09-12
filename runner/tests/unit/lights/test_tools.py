from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from atlas.lights.application.registry import LightsRegistry
from atlas.lights.application.service import LightsService
from atlas.lights.application.tools import LightsTools
from atlas.lights.infrastructure.stub_controller import StubMatterController
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"


async def _tools() -> tuple[LightsTools, StubMatterController]:
    stub = StubMatterController()
    service = LightsService(
        registry=LightsRegistry.load(FIXTURE),
        controller=stub,
        bus=InProcessEventBus(),
        clock=FrozenClock(datetime(2026, 9, 12, 20, 0, tzinfo=UTC)),
    )
    await service.start()
    return LightsTools(service), stub


async def test_set_lights_expands_groups_and_reports() -> None:
    tools, _ = await _tools()
    result = await tools.set_lights(["bedroom"], {"brightness": 40, "color_temp_k": 2700})
    assert sorted(result["applied"]) == ["ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4"]  # type: ignore[type-var, arg-type]
    assert result["failed"] == []
    assert result["states"]["ceiling-2"]["brightness"] == 40  # type: ignore[call-overload, index]


async def test_toggle() -> None:
    tools, _ = await _tools()
    result = await tools.set_lights(["ceiling-1"], {}, toggle=True)
    assert result["states"]["ceiling-1"]["on"] is True  # type: ignore[call-overload, index]


async def test_unknown_target_is_reported_not_raised() -> None:
    tools, _ = await _tools()
    result = await tools.set_lights(["kitchen", "ceiling-1"], {"on": True})
    assert result["applied"] == ["ceiling-1"]
    assert result["failed"] == ["kitchen"]
    assert "kitchen" in result["errors"]  # type: ignore[operator]


async def test_scene_and_state() -> None:
    tools, _ = await _tools()
    scene = await tools.activate_scene("off")
    assert scene["failed"] == []
    assert tools.state()["lights"]["ceiling-1"]["on"] is False  # type: ignore[call-overload, index]
