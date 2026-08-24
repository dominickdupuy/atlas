"""InMemoryEventBus: records envelopes instead of publishing them.

Used by tests and by broker-less local runs (`pihome serve` outside
compose); the dashboard still works because SSE feeds off the in-process
stream, not MQTT.
"""

from __future__ import annotations

from pihome.telemetry.domain.envelope import EventEnvelope


class InMemoryEventBus:
    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, envelope: EventEnvelope) -> None:
        self.published.append(envelope)

    def topics(self) -> list[str]:
        return [envelope.topic for envelope in self.published]
