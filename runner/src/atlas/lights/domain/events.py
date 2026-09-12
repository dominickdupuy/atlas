"""Lights domain events. One event, published for every attribute change the
controller reports, from any fabric (spec 11): state, not provenance."""

from __future__ import annotations

from atlas.lights.domain.model import LightState
from atlas.shared.events import DomainEvent


class LightChanged(DomainEvent):
    name: str
    state: LightState


class ControllerConnectivityChanged(DomainEvent):
    """Published on the transition only, never per retry (spec 11)."""

    connected: bool
