"""LightsService against the stub (spec 4.2). The cache holds only what the
device confirmed; atlas's own writes come back through the same path as
Apple Home's."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.lights.application.ports import ControllerUnavailable
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.application.service import LightsService, UnknownLight, UnknownScene
from atlas.lights.domain.events import ControllerConnectivityChanged, LightChanged
from atlas.lights.domain.matter import LEVEL_CLUSTER
from atlas.lights.domain.model import LightCommand, UnsupportedFeature
from atlas.lights.infrastructure.stub_controller import StubMatterController
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus
from tests.fakes import EventRecorder

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"
NOW = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)


async def _service(
    controller: StubMatterController | None = None, **kwargs: object
) -> tuple[LightsService, StubMatterController, EventRecorder, InProcessEventBus]:
    stub = controller or StubMatterController()
    bus = InProcessEventBus()
    recorder = EventRecorder(bus)
    service = LightsService(
        registry=LightsRegistry.load(FIXTURE),
        controller=stub,
        bus=bus,
        clock=FrozenClock(NOW),
        **kwargs,  # type: ignore[arg-type]
    )
    await service.start()
    return service, stub, recorder, bus


async def test_start_seeds_the_cache_from_the_controller() -> None:
    service, _, _, _ = await _service()
    snapshot = service.snapshot()
    assert set(snapshot.lights) == {"ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4"}
    assert snapshot.lights["ceiling-1"].on is False
    assert snapshot.lights["ceiling-1"].brightness == 100
    assert snapshot.error is None
    assert snapshot.fetched_at == NOW


async def test_apply_sends_the_plan_and_returns_confirmed_state() -> None:
    service, stub, recorder, _ = await _service()
    state = await service.apply("ceiling-2", LightCommand(brightness=40, color_temp_k=2700))
    assert [s[3] for s in stub.sent] == ["moveToColorTemperature", "moveToLevelWithOnOff", "on"]
    assert state.on is True
    assert state.brightness == 40
    assert state.color_temp_k == 2703
    changed = [e for e in recorder.of_type(LightChanged) if e.name == "ceiling-2"]  # type: ignore[attr-defined]
    assert changed, "every confirmed attribute change publishes LightChanged"


async def test_apply_never_writes_the_cache_optimistically() -> None:
    """A controller that accepts the command but never confirms it leaves
    the cache untouched and the error set; the bulb may still have obeyed."""
    stub = StubMatterController()

    async def silent_send(*args: object, **kwargs: object) -> None:
        return None

    stub.send = silent_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=0.01)
    state = await service.apply("ceiling-1", LightCommand(on=True))
    assert state.on is False, "unconfirmed: cache keeps the last confirmed value"
    assert service.snapshot().lights["ceiling-1"].on is False


async def test_apply_color_temp_on_an_off_bulb_lands_the_colour_and_turns_it_on() -> None:
    """Regression: plan() must send optionsMask/Override=1/1 on ColorControl
    commands, or a bulb that starts off drops the colour command and comes
    on later at its old colour (spec 4.1)."""
    service, _, _, _ = await _service()
    assert service.get("ceiling-1").on is False
    state = await service.apply("ceiling-1", LightCommand(color_temp_k=2700))
    assert state.on is True
    assert state.color_temp_k == 2703


async def test_toggle_flips_power() -> None:
    service, _, _, _ = await _service()
    assert (await service.toggle("ceiling-3")).on is True
    assert (await service.toggle("ceiling-3")).on is False


async def test_external_change_updates_cache_and_publishes() -> None:
    service, stub, recorder, _ = await _service()
    await stub.emit_attribute(4, "1/6/0", True)
    assert service.get("ceiling-4").on is True
    events = recorder.of_type(LightChanged)
    assert any(e.name == "ceiling-4" and e.state.on is True for e in events)  # type: ignore[attr-defined]


async def test_unknown_light_and_scene() -> None:
    service, _, _, _ = await _service()
    with pytest.raises(UnknownLight):
        await service.apply("kitchen", LightCommand(on=True))
    with pytest.raises(UnknownScene):
        await service.activate("party")


async def test_unsupported_feature_bubbles_up() -> None:
    stub = StubMatterController()
    plug = stub._nodes[1].model_copy(update={"attributes": {"1/6/0": False}})
    stub._nodes[1] = plug
    service, _, _, _ = await _service(stub)
    with pytest.raises(UnsupportedFeature):
        await service.apply("ceiling-1", LightCommand(brightness=10))


async def test_controller_down_is_a_503_shaped_error_and_a_snapshot_error() -> None:
    stub = StubMatterController()
    service, _, _, _ = await _service(stub)
    await service.on_disconnected()
    assert service.snapshot().error == "controller unavailable"
    with pytest.raises(ControllerUnavailable):
        await service.apply("ceiling-1", LightCommand(on=True))


async def test_connectivity_event_only_on_the_transition() -> None:
    service, stub, recorder, _ = await _service()
    await service.on_disconnected()
    await service.on_disconnected()
    await service.on_connected(await stub.nodes())
    flips = [e.connected for e in recorder.of_type(ControllerConnectivityChanged)]  # type: ignore[attr-defined]
    assert flips == [True, False, True], "start() connects once; two disconnects count once"


async def test_reconnect_replaces_the_cache_from_the_node_dump() -> None:
    service, stub, _, _ = await _service()
    await service.on_disconnected()
    changed = stub._nodes[2].model_copy(
        update={"attributes": {**stub._nodes[2].attributes, "1/6/0": True}}
    )
    await service.on_connected([stub._nodes[1], changed, stub._nodes[3], stub._nodes[4]])
    assert service.snapshot().error is None
    assert service.get("ceiling-2").on is True


async def test_scene_fans_out_and_reports_partial_failure() -> None:
    stub = StubMatterController()
    original_send = stub.send

    async def flaky_send(
        node_id: int, endpoint_id: int, cluster_id: int, name: str, payload: dict[str, int]
    ) -> None:
        if node_id == 3:
            return None  # never confirms
        await original_send(node_id, endpoint_id, cluster_id, name, payload)

    stub.send = flaky_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=0.01)
    result = await service.activate("evening")
    assert result.applied == ("ceiling-1", "ceiling-2", "ceiling-4")
    assert result.failed == ("ceiling-3",)
    assert result.snapshot.lights["ceiling-1"].brightness == 40


async def test_reconciliation_reads_and_corrects_a_missed_report() -> None:
    stub = StubMatterController()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) > 1:
            raise asyncio.CancelledError

    service, _, _, _ = await _service(stub, reconcile_interval=300.0, sleep=fake_sleep)
    # Change the device behind the subscription's back.
    node = stub._nodes[1]
    stub._nodes[1] = node.model_copy(update={"attributes": {**node.attributes, "1/6/0": True}})
    with pytest.raises(asyncio.CancelledError):
        await service.run()
    assert slept[0] == 300.0
    assert service.get("ceiling-1").on is True


# --- fix round 1 -----------------------------------------------------------


async def test_disconnect_during_apply_never_confirms() -> None:
    """A disconnect sets every pending waiter, but that is a wake, not a
    confirmation: the state never actually satisfied the command."""
    stub = StubMatterController()

    async def silent_send(*args: object, **kwargs: object) -> None:
        return None

    stub.send = silent_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=1.0)

    async def disconnect_soon() -> None:
        await asyncio.sleep(0)
        await service.on_disconnected()

    disconnector = asyncio.create_task(disconnect_soon())
    state, confirmed = await service._apply("ceiling-1", LightCommand(on=True))
    await disconnector
    assert confirmed is False, "a disconnect wake must never confirm"
    assert state.on is False


async def test_disconnect_during_activate_reports_failure() -> None:
    stub = StubMatterController()

    async def silent_send(*args: object, **kwargs: object) -> None:
        return None

    stub.send = silent_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=1.0)

    async def disconnect_soon() -> None:
        await asyncio.sleep(0)
        await service.on_disconnected()

    disconnector = asyncio.create_task(disconnect_soon())
    result = await service.activate("night")
    await disconnector
    assert "ceiling-1" in result.failed


async def test_removed_node_requires_recommissioning_before_apply() -> None:
    """A removed light takes the never-seen fast path: no cached
    capabilities, so a command against it is a 503, not a plan()."""
    service, stub, _, _ = await _service()
    await stub.remove_node(2)
    with pytest.raises(ControllerUnavailable, match="ceiling-2"):
        await service.apply("ceiling-2", LightCommand(on=True))


async def test_apply_partial_multi_command_confirmation_is_not_confirmed() -> None:
    """A multi-command apply must wait for every field it asked for, not
    return on the first attribute event: colour and power confirm, but the
    level command is silently dropped, so the whole apply must fail."""
    stub = StubMatterController()
    original_send = stub.send

    async def level_silent_send(
        node_id: int, endpoint_id: int, cluster_id: int, name: str, payload: dict[str, int]
    ) -> None:
        if cluster_id == LEVEL_CLUSTER:
            return None  # never confirms brightness
        await original_send(node_id, endpoint_id, cluster_id, name, payload)

    stub.send = level_silent_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=0.05)

    state = await service.apply("ceiling-1", LightCommand(brightness=40, color_temp_k=2700))
    assert state.brightness == 100, "unconfirmed: brightness keeps the last confirmed value"
    assert state.on is True
    assert state.color_temp_k == 2703

    result = await service.activate("evening")
    assert "ceiling-1" in result.failed


async def test_apply_already_satisfied_confirms_without_waiting() -> None:
    """A bulb already in the requested state confirms with no attribute
    event at all: `send` is silent throughout, yet the multi-field command
    it carries is already true of the device."""
    stub = StubMatterController()
    node = stub._nodes[1]
    stub._nodes[1] = node.model_copy(update={"attributes": {**node.attributes, "1/6/0": True}})

    async def silent_send(*args: object, **kwargs: object) -> None:
        return None

    stub.send = silent_send  # type: ignore[method-assign]
    service, _, _, _ = await _service(stub, confirm_timeout=0.05)
    state, confirmed = await service._apply("ceiling-1", LightCommand(brightness=100))
    assert confirmed is True
    assert state.on is True
    assert state.brightness == 100
