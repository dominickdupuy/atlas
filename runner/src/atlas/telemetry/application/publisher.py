"""MqttPublisherService: in-process domain events → MQTT envelopes (D6).

Subscribes to the in-process bus, maps each event through topics.py, and
hands envelopes to the broker adapter. Publish failures are logged, never
raised — MQTT being down must not fail a job (the board runs off the
in-process stream anyway).
"""

from __future__ import annotations

import logging

from atlas.shared.events import DomainEvent, InProcessEventBus
from atlas.telemetry.application.ports import EventBusPort
from atlas.telemetry.domain.topics import envelope_for

logger = logging.getLogger(__name__)


class MqttPublisherService:
    def __init__(self, bus: InProcessEventBus, mqtt: EventBusPort) -> None:
        self._mqtt = mqtt
        bus.subscribe(self._on_event)

    async def _on_event(self, event: DomainEvent) -> None:
        envelope = envelope_for(event)
        if envelope is None:
            return
        try:
            await self._mqtt.publish(envelope)
        except Exception:
            logger.exception("MQTT publish failed for %s", envelope.topic)
