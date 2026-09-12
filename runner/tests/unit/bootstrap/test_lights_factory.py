from __future__ import annotations

from pathlib import Path

from atlas.bootstrap.lights_factory import build_lights, matter_probe
from atlas.config import Settings
from atlas.lights.infrastructure.matter_ws import MatterWsClient
from atlas.shared.clock import SystemClock
from atlas.shared.events import InProcessEventBus

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"


def _settings(url: str) -> Settings:
    return Settings(_env_file=None, matter_ws_url=url, lights_file=FIXTURE)


def test_empty_url_means_no_lights() -> None:
    assert build_lights(_settings(""), InProcessEventBus(), SystemClock()) == (None, None)
    assert matter_probe(_settings("")) is None


def test_stub_url_wires_the_stub_and_no_client() -> None:
    service, client = build_lights(_settings("stub"), InProcessEventBus(), SystemClock())
    assert service is not None
    assert client is None
    assert matter_probe(_settings("stub")) is None


def test_ws_url_wires_the_real_client_and_a_probe() -> None:
    service, client = build_lights(
        _settings("ws://127.0.0.1:5580/ws"), InProcessEventBus(), SystemClock()
    )
    assert service is not None
    assert isinstance(client, MatterWsClient)
    probe = matter_probe(_settings("ws://127.0.0.1:5580/ws"))
    assert probe is not None
    assert probe.name == "matter"
    assert probe.endpoint == "127.0.0.1:5580"
