"""Lights wiring (D20). Gated on ATLAS_MATTER_WS_URL, not on the profile:
empty is off, "stub" is four in-memory bulbs, a ws:// URL is the controller."""

from __future__ import annotations

from urllib.parse import urlsplit

from atlas.config import Settings
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.application.service import LightsService
from atlas.lights.infrastructure.matter_ws import MatterWsClient
from atlas.lights.infrastructure.stub_controller import StubMatterController
from atlas.shared.clock import Clock
from atlas.shared.events import InProcessEventBus
from atlas.telemetry.infrastructure.service_probes import TcpServiceProbe

STUB_URL = "stub"


def build_lights(
    settings: Settings, bus: InProcessEventBus, clock: Clock
) -> tuple[LightsService | None, MatterWsClient | None]:
    if not settings.matter_ws_url:
        return None, None
    registry = LightsRegistry.load(settings.lights_file)
    if settings.matter_ws_url == STUB_URL:
        service = LightsService(
            registry=registry, controller=StubMatterController(), bus=bus, clock=clock
        )
        return service, None
    client = MatterWsClient(settings.matter_ws_url)
    service = LightsService(registry=registry, controller=client, bus=bus, clock=clock)
    return service, client


def matter_probe(settings: Settings) -> TcpServiceProbe | None:
    if not settings.matter_ws_url or settings.matter_ws_url == STUB_URL:
        return None
    parts = urlsplit(settings.matter_ws_url)
    return TcpServiceProbe("matter", parts.hostname or "127.0.0.1", parts.port or 5580)
