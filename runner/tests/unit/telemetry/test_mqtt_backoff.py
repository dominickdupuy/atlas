"""Reconnect backoff, with no broker anywhere near the test.

The live-broker path is covered by tests/integration/test_mqtt_bus.py, which
is marked `mqtt` and excluded from CI. These tests drive the same loop
through an injected client factory, so the failure path — the one that only
happens when Mosquitto is down and is therefore never exercised by hand — is
the part that actually gets tested.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import aiomqtt
import pytest

from atlas.telemetry.domain.envelope import EventEnvelope
from atlas.telemetry.infrastructure.mqtt_bus import (
    AiomqttEventBus,
    MqttClient,
    ReconnectBackoff,
)

# --- the pure policy -------------------------------------------------------


def test_delays_grow_exponentially_and_stop_at_the_ceiling() -> None:
    backoff = ReconnectBackoff(initial=1.0, maximum=60.0, factor=2.0, jitter=lambda: 1.0)
    delays = [backoff.next_delay() for _ in range(10)]
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert max(delays) == 60.0, "a broker down for an hour must not retry every 5s forever"


def test_jitter_stays_inside_the_top_half_of_the_window() -> None:
    """Never a busy loop: even the unluckiest draw waits half the window."""
    floor = ReconnectBackoff(initial=8.0, jitter=lambda: 0.0).next_delay()
    ceiling = ReconnectBackoff(initial=8.0, jitter=lambda: 1.0).next_delay()
    assert floor == 4.0
    assert ceiling == 8.0


def test_reset_returns_to_the_first_window() -> None:
    backoff = ReconnectBackoff(initial=1.0, jitter=lambda: 1.0)
    for _ in range(5):
        backoff.next_delay()
    backoff.reset()
    assert backoff.next_delay() == 1.0
    assert backoff.attempts == 1


# --- the loop, against a fake broker ---------------------------------------


class _FakeClient:
    def __init__(self, published: list[tuple[str, str, int]], delivered: asyncio.Event) -> None:
        self._published = published
        self._delivered = delivered

    async def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        self._published.append((topic, payload, qos))
        self._delivered.set()


class _FailingConnection(AbstractAsyncContextManager[MqttClient]):
    async def __aenter__(self) -> MqttClient:
        raise aiomqtt.MqttError("broker unavailable")

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _WorkingConnection(AbstractAsyncContextManager[MqttClient]):
    def __init__(self, client: MqttClient) -> None:
        self._client = client

    async def __aenter__(self) -> MqttClient:
        return self._client

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FlakyFactory:
    """Refuses `failures` connections, then works."""

    def __init__(self, failures: int, published: list[tuple[str, str, int]]) -> None:
        self._failures = failures
        self._published = published
        self.connect_attempts = 0
        self.delivered = asyncio.Event()

    def __call__(self) -> AbstractAsyncContextManager[MqttClient]:
        self.connect_attempts += 1
        if self.connect_attempts <= self._failures:
            return _FailingConnection()
        return _WorkingConnection(_FakeClient(self._published, self.delivered))


def _envelope(topic: str = "atlas/jobs/x/completed") -> EventEnvelope:
    return EventEnvelope(
        topic=topic,
        payload={"n": 1},
        occurred_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
    )


async def _drain(bus: AiomqttEventBus, factory: _FlakyFactory) -> None:
    """Run the loop until the fake broker has taken one message, then stop."""
    task = asyncio.create_task(bus.run())
    try:
        async with asyncio.timeout(5):
            await factory.delivered.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_retries_with_growing_delays_then_delivers() -> None:
    published: list[tuple[str, str, int]] = []
    factory = _FlakyFactory(failures=3, published=published)
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    backoff = ReconnectBackoff(initial=1.0, jitter=lambda: 1.0)
    bus = AiomqttEventBus(
        "broker.invalid", 1883, client_factory=factory, sleep=fake_sleep, backoff=backoff
    )
    await bus.publish(_envelope())

    await _drain(bus, factory)

    assert slept == [1.0, 2.0, 4.0], "each failed attempt must wait longer than the last"
    assert factory.connect_attempts == 4
    assert len(published) == 1, "the envelope queued during the outage still gets delivered"


async def test_backoff_resets_once_connected() -> None:
    """A blip after a long outage must not inherit the old ceiling."""
    published: list[tuple[str, str, int]] = []
    factory = _FlakyFactory(failures=2, published=published)

    async def fake_sleep(_: float) -> None:
        return None

    backoff = ReconnectBackoff(initial=1.0, jitter=lambda: 1.0)
    bus = AiomqttEventBus(
        "broker.invalid", 1883, client_factory=factory, sleep=fake_sleep, backoff=backoff
    )
    await bus.publish(_envelope())

    await _drain(bus, factory)

    assert backoff.attempts == 0
    assert backoff.next_delay() == 1.0


async def test_payload_carries_a_timestamp() -> None:
    published: list[tuple[str, str, int]] = []
    factory = _FlakyFactory(failures=0, published=published)

    async def fake_sleep(_: float) -> None:
        return None

    bus = AiomqttEventBus("broker.invalid", 1883, client_factory=factory, sleep=fake_sleep)
    await bus.publish(_envelope("atlas/system/health"))

    await _drain(bus, factory)

    topic, payload, qos = published[0]
    assert topic == "atlas/system/health"
    assert qos == 1, "terminal events are at-least-once (D6/section 8)"
    assert "2026-09-01T03:00:00+00:00" in payload


@pytest.mark.parametrize("failures", [1, 5])
async def test_delivery_survives_any_number_of_failed_connections(failures: int) -> None:
    published: list[tuple[str, str, int]] = []
    factory = _FlakyFactory(failures=failures, published=published)

    async def fake_sleep(_: float) -> None:
        return None

    bus = AiomqttEventBus("broker.invalid", 1883, client_factory=factory, sleep=fake_sleep)
    await bus.publish(_envelope())

    await _drain(bus, factory)

    assert len(published) == 1
    assert factory.connect_attempts == failures + 1
