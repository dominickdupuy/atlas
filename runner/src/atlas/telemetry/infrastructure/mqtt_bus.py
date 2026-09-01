"""AiomqttEventBus: envelopes → Mosquitto, publish-only (D6).

Envelopes go onto an internal queue; a background task drains it and owns
the broker connection with reconnect-and-backoff. Publishing therefore never
blocks a job, and a broker outage costs at most the queue's tail (dropped
oldest-first, loudly) — never a terminal event in SQLite.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from atlas.telemetry.domain.envelope import EventEnvelope

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 1000
_RECONNECT_BACKOFF_SECONDS = 5.0


class AiomqttEventBus:
    def __init__(self, host: str, port: int, client_id: str = "atlas-runner") -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=_QUEUE_SIZE)

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
                async with aiomqtt.Client(
                    self._host, self._port, identifier=self._client_id
                ) as client:
                    logger.info("connected to MQTT broker at %s:%d", self._host, self._port)
                    while True:
                        envelope = await self._queue.get()
                        payload = dict(envelope.payload)
                        payload["ts"] = envelope.occurred_at.isoformat()
                        await client.publish(envelope.topic, json.dumps(payload), qos=1)
            except aiomqtt.MqttError as exc:
                logger.warning(
                    "MQTT connection lost (%s); retrying in %.0fs",
                    exc,
                    _RECONNECT_BACKOFF_SECONDS,
                )
                await asyncio.sleep(_RECONNECT_BACKOFF_SECONDS)
