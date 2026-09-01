"""AiomqttEventBus: envelopes → Mosquitto, publish-only (D6).

Envelopes go onto an internal queue; a background task drains it and owns
the broker connection with reconnect-and-backoff. Publishing therefore never
blocks a job, and a broker outage costs at most the queue's tail (dropped
oldest-first, loudly) — never a terminal event in SQLite.

Reconnection backs off exponentially. A fixed short retry against a broker
that is down for maintenance is just a log-spam generator, and on this host
the broker and the runner restart together often enough for that to matter.
The delay resets on every successful connection, so an hour of uptime
followed by a blip retries promptly rather than inheriting an old ceiling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

import aiomqtt

from atlas.telemetry.domain.envelope import EventEnvelope

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 1000
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
BACKOFF_FACTOR = 2.0


class MqttClient(Protocol):
    """The one method this module needs from a broker client."""

    async def publish(self, topic: str, payload: str, qos: int = 0) -> None: ...


ClientFactory = Callable[[], AbstractAsyncContextManager[MqttClient]]
Sleeper = Callable[[float], Awaitable[None]]


class ReconnectBackoff:
    """Exponential backoff with partial jitter.

    The delay is drawn from the top half of the current window (50-100% of
    it), which spreads retries without ever collapsing back to a busy loop
    the way full jitter can.
    """

    def __init__(
        self,
        *,
        initial: float = INITIAL_BACKOFF_SECONDS,
        maximum: float = MAX_BACKOFF_SECONDS,
        factor: float = BACKOFF_FACTOR,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._jitter = jitter
        self._attempts = 0

    @property
    def attempts(self) -> int:
        return self._attempts

    def reset(self) -> None:
        self._attempts = 0

    def next_delay(self) -> float:
        window = min(self._maximum, self._initial * self._factor**self._attempts)
        self._attempts += 1
        return window * (0.5 + 0.5 * self._jitter())


class AiomqttEventBus:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str = "atlas-runner",
        *,
        client_factory: ClientFactory | None = None,
        sleep: Sleeper = asyncio.sleep,
        backoff: ReconnectBackoff | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._client_factory: ClientFactory = client_factory or self._default_client
        self._sleep = sleep
        self._backoff = backoff or ReconnectBackoff()

    def _default_client(self) -> AbstractAsyncContextManager[MqttClient]:
        return aiomqtt.Client(self._host, self._port, identifier=self._client_id)

    async def publish(self, envelope: EventEnvelope) -> None:
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            dropped = self._queue.get_nowait()
            logger.warning("MQTT queue full; dropped oldest envelope for %s", dropped.topic)
            self._queue.put_nowait(envelope)

    async def run(self) -> None:
        """Connection-owning loop; run as a background task."""
        while True:
            try:
                async with self._client_factory() as client:
                    logger.info("connected to MQTT broker at %s:%d", self._host, self._port)
                    self._backoff.reset()
                    while True:
                        envelope = await self._queue.get()
                        payload = dict(envelope.payload)
                        payload["ts"] = envelope.occurred_at.isoformat()
                        await client.publish(envelope.topic, json.dumps(payload), qos=1)
            except aiomqtt.MqttError as exc:
                delay = self._backoff.next_delay()
                logger.warning(
                    "MQTT connection lost (%s); retry %d in %.1fs",
                    exc,
                    self._backoff.attempts,
                    delay,
                )
                await self._sleep(delay)
