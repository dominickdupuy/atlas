"""LightsService (spec 4.2): the application face of the lights context.

The cache holds only device-confirmed state. `apply` sends the plan and then
waits, bounded, for the controller to report the resulting attributes; it
never writes the requested values in optimistically. Atlas's own writes and
Apple Home's therefore reach the cache, the bus and MQTT through one path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from atlas.lights.application.ports import (
    ControllerUnavailable,
    MatterController,
    MatterNode,
)
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.domain.events import ControllerConnectivityChanged, LightChanged
from atlas.lights.domain.matter import (
    RELEVANT_CLUSTERS,
    capabilities_from,
    decode,
    plan,
    satisfies,
)
from atlas.lights.domain.model import (
    Light,
    LightCapabilities,
    LightCommand,
    LightState,
    Scene,
)
from atlas.shared.clock import Clock
from atlas.shared.events import InProcessEventBus

logger = logging.getLogger(__name__)

Sleeper = Callable[[float], Awaitable[None]]

CONTROLLER_UNAVAILABLE = "controller unavailable"


class UnknownLight(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown light {name!r}")
        self.name = name


class UnknownScene(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown scene {name!r}")
        self.name = name


class LightsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    lights: dict[str, LightState]
    fetched_at: datetime | None = None
    error: str | None = None


class ApplyOutcome(BaseModel):
    """Result of a single-light write (spec 4.2). `error` is set exactly
    when `confirmed` is False: the controller accepted the plan, but no
    matching attribute event arrived within the confirmation timeout. The
    bulb may still have obeyed; `state` is the last-known state either way,
    never the requested one."""

    model_config = ConfigDict(frozen=True)

    state: LightState
    confirmed: bool
    error: str | None = None


class SceneResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene: str
    applied: tuple[str, ...]
    failed: tuple[str, ...]
    snapshot: LightsSnapshot


class _Entry:
    """Mutable per-light cache line."""

    def __init__(self, light: Light) -> None:
        self.light = light
        self.attributes: dict[str, JsonValue] = {}
        self.capabilities: LightCapabilities | None = None
        self.available = False
        self.state = LightState(reachable=False)
        self.waiters: list[asyncio.Event] = []


class LightsService:
    def __init__(
        self,
        *,
        registry: LightsRegistry,
        controller: MatterController,
        bus: InProcessEventBus,
        clock: Clock,
        confirm_timeout: float = 1.0,
        reconcile_interval: float = 300.0,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._registry = registry
        self._controller = controller
        self._bus = bus
        self._clock = clock
        self._confirm_timeout = confirm_timeout
        self._reconcile_interval = reconcile_interval
        self._sleep = sleep
        self._entries: dict[str, _Entry] = {
            name: _Entry(light) for name, light in registry.lights.items()
        }
        self._connected = False
        self._fetched_at: datetime | None = None

    @property
    def registry(self) -> LightsRegistry:
        return self._registry

    # --- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        self._controller.subscribe(self)
        if self._controller.connected:
            await self.on_connected(await self._controller.nodes())

    async def run(self) -> None:
        """Reconciliation loop (spec 4.2): a read per light every interval,
        applied through the same decode path. Runs as a lifespan task."""
        while True:
            await self._sleep(self._reconcile_interval)
            if not self._connected:
                continue
            for entry in self._entries.values():
                try:
                    reported = await self._controller.read(
                        entry.light.node_id, f"{entry.light.endpoint_id}/*/*"
                    )
                except ControllerUnavailable:
                    break
                except Exception:
                    logger.exception("reconciliation read failed for %s", entry.light.name)
                    continue
                for path, value in reported.items():
                    cluster = int(path.split("/")[1])
                    if cluster in RELEVANT_CLUSTERS and entry.attributes.get(path) != value:
                        logger.info(
                            "reconciliation: %s %s was %r, device says %r",
                            entry.light.name,
                            path,
                            entry.attributes.get(path),
                            value,
                        )
                        await self.on_attribute(entry.light.node_id, path, value)

    # --- ControllerListener ------------------------------------------------

    async def on_connected(self, nodes: list[MatterNode]) -> None:
        await self._set_connected(True)
        by_node = {node.node_id: node for node in nodes}
        for entry in self._entries.values():
            node = by_node.get(entry.light.node_id)
            if node is None:
                entry.available = False
                entry.attributes = {}
                entry.capabilities = None
                await self._refresh(entry)
                continue
            entry.attributes = dict(node.attributes)
            entry.available = node.available
            entry.capabilities = capabilities_from(entry.light, entry.attributes)
            await self._refresh(entry)
        self._fetched_at = self._clock.now()

    async def on_disconnected(self) -> None:
        await self._set_connected(False)
        for entry in self._entries.values():
            for waiter in entry.waiters:
                waiter.set()

    async def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        await self._bus.publish(
            ControllerConnectivityChanged(occurred_at=self._clock.now(), connected=connected)
        )

    async def on_node(self, node: MatterNode) -> None:
        light = self._registry.by_node(node.node_id)
        if light is None:
            return
        entry = self._entries[light.name]
        entry.attributes = dict(node.attributes)
        entry.available = node.available
        entry.capabilities = capabilities_from(light, entry.attributes)
        await self._refresh(entry)

    async def on_node_removed(self, node_id: int) -> None:
        light = self._registry.by_node(node_id)
        if light is None:
            return
        entry = self._entries[light.name]
        entry.available = False
        entry.capabilities = None
        await self._refresh(entry)

    async def on_attribute(self, node_id: int, path: str, value: JsonValue) -> None:
        light = self._registry.by_node(node_id)
        if light is None:
            return
        entry = self._entries[light.name]
        entry.attributes[path] = value
        if entry.capabilities is None:
            entry.capabilities = capabilities_from(light, entry.attributes)
        await self._refresh(entry)

    async def _refresh(self, entry: _Entry) -> None:
        now = self._clock.now()
        entry.state = decode(
            entry.light,
            entry.attributes,
            reachable=entry.available and self._connected,
            observed_at=now,
        )
        for waiter in entry.waiters:
            waiter.set()
        await self._bus.publish(
            LightChanged(occurred_at=now, name=entry.light.name, state=entry.state)
        )

    # --- queries -----------------------------------------------------------

    def snapshot(self) -> LightsSnapshot:
        return LightsSnapshot(
            lights={name: entry.state for name, entry in self._entries.items()},
            fetched_at=self._fetched_at,
            error=None if self._connected else CONTROLLER_UNAVAILABLE,
        )

    def get(self, name: str) -> LightState:
        return self._entry(name).state

    def scenes(self) -> list[Scene]:
        return list(self._registry.scenes.values())

    def resolve(self, target: str) -> list[str]:
        return self._registry.resolve(target)

    # --- commands ----------------------------------------------------------

    async def apply(self, name: str, command: LightCommand) -> ApplyOutcome:
        """Send the plan and wait, bounded, for the device to confirm it.

        An unconfirmed write is not an exception: the controller accepted
        the plan, so the caller gets the last-known state back with `error`
        set, per spec 4.2, rather than a failure that hides a bulb that may
        still have obeyed. `activate` reads `confirmed` off the outcome
        (not just an unchanged state, which a bulb already in the requested
        state would also produce) to tell a confirmed no-op from a failed
        one. Confirmed is *not* "an attribute-change event fired": a
        multi-command apply can wake on a partial confirmation, and a
        disconnect wakes every waiter without confirming anything.
        """
        entry = self._entry(name)
        if not self._connected:
            raise ControllerUnavailable(CONTROLLER_UNAVAILABLE)
        if entry.capabilities is None:
            raise ControllerUnavailable(f"{name} has not been seen by the controller")
        commands = plan(entry.light, entry.capabilities, command)
        confirmed = asyncio.Event()
        entry.waiters.append(confirmed)
        try:
            for cluster_command in commands:
                await self._controller.send(
                    entry.light.node_id,
                    cluster_command.endpoint_id,
                    cluster_command.cluster_id,
                    cluster_command.name,
                    cluster_command.payload,
                )
            ok = await self._await_satisfied(entry, command, confirmed)
        finally:
            entry.waiters.remove(confirmed)
        error = None if ok else f"no confirmation within {self._confirm_timeout:.1f}s"
        return ApplyOutcome(state=entry.state, confirmed=ok, error=error)

    async def _await_satisfied(
        self, entry: _Entry, command: LightCommand, confirmed: asyncio.Event
    ) -> bool:
        """Wait, bounded, until `entry.state` satisfies `command`.

        Checked before ever waiting, so a bulb already in the requested
        state confirms with no event at all. Each wake (a real attribute
        change, a partial confirmation, or a disconnect) rechecks rather
        than trusting the event; a wake found while disconnected never
        confirms.
        """
        deadline = time.monotonic() + self._confirm_timeout
        while True:
            if satisfies(entry.state, command):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "%s: no confirmation within %.1fs", entry.light.name, self._confirm_timeout
                )
                return False
            try:
                async with asyncio.timeout(remaining):
                    await confirmed.wait()
            except TimeoutError:
                logger.warning(
                    "%s: no confirmation within %.1fs", entry.light.name, self._confirm_timeout
                )
                return False
            if not self._connected:
                return False
            confirmed.clear()

    async def toggle(self, name: str) -> ApplyOutcome:
        current = self._entry(name).state
        return await self.apply(name, LightCommand(on=not current.on))

    async def activate(self, scene_name: str) -> SceneResult:
        scene = self._registry.scenes.get(scene_name)
        if scene is None:
            raise UnknownScene(scene_name)

        async def one(name: str, command: LightCommand) -> tuple[str, bool]:
            try:
                outcome = await self.apply(name, command)
            except Exception:
                logger.exception("scene %s: %s failed", scene_name, name)
                return name, False
            return name, outcome.confirmed

        results = await asyncio.gather(*(one(n, c) for n, c in scene.states.items()))
        applied = tuple(name for name, ok in results if ok)
        failed = tuple(name for name, ok in results if not ok)
        return SceneResult(
            scene=scene_name,
            applied=applied,
            failed=failed,
            snapshot=self.snapshot(),
        )

    def _entry(self, name: str) -> _Entry:
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownLight(name)
        return entry
