"""AiomqttEventBus against a live broker. Marked `mqtt`: excluded in CI, run
locally with the compose Mosquitto up:

    docker compose up -d mosquitto
    uv run pytest -m mqtt
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import aiomqtt
import pytest

from atlas.telemetry.domain.envelope import EventEnvelope
from atlas.telemetry.infrastructure.mqtt_bus import AiomqttEventBus

pytestmark = pytest.mark.mqtt

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883


async def test_publish_reaches_a_subscriber() -> None:
    bus = AiomqttEventBus(BROKER_HOST, BROKER_PORT, client_id="atlas-test-pub")
    runner = asyncio.create_task(bus.run())
    try:
        async with aiomqtt.Client(
            BROKER_HOST, BROKER_PORT, identifier="atlas-test-sub"
        ) as subscriber:
            await subscriber.subscribe("atlas/test/#")
            await bus.publish(
                EventEnvelope(
                    topic="atlas/test/hello",
                    payload={"n": 1},
                    occurred_at=datetime.now(UTC),
                )
            )
            async with asyncio.timeout(10):
                async for message in subscriber.messages:
                    assert message.topic.matches("atlas/test/hello")
                    assert isinstance(message.payload, bytes)
                    body = json.loads(message.payload)
                    assert body["n"] == 1
                    assert "ts" in body
                    break
    finally:
        runner.cancel()
