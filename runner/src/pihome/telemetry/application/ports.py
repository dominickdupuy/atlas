"""Ports of the telemetry context."""

from __future__ import annotations

from typing import Protocol

from pihome.telemetry.domain.envelope import EventEnvelope


class EventBusPort(Protocol):
    """Outbound-only (D6): the runner publishes state; commands never arrive
    this way (D16)."""

    async def publish(self, envelope: EventEnvelope) -> None: ...
