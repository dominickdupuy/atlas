"""The Intent contract (D26). Both tiers emit it; the service cannot tell
which tier produced it. validate_intent() is the gate before any actuation:
never act on an unvalidated parse."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.lights.application.registry import ALL, LightsRegistry
from atlas.lights.domain.colour import NAMED_COLOURS, TEMPERATURE_WORDS


class IntentKind(StrEnum):
    SET_LIGHT = "set_light"
    APPLY_SCENE = "apply_scene"
    QUERY = "query"
    UNKNOWN = "unknown"


class StateWords(BaseModel):
    """Words, not numbers (D28): the lights domain turns "red" into hue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    power: Literal["on", "off", "toggle"] | None = None
    brightness_pct: int | None = Field(default=None, ge=0, le=100)
    color: str | None = None

    @property
    def empty(self) -> bool:
        return self.power is None and self.brightness_pct is None and self.color is None


class Intent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: IntentKind
    targets: tuple[str, ...] = ()
    state: StateWords | None = None
    scene: str | None = None


UNKNOWN_INTENT = Intent(intent=IntentKind.UNKNOWN)


class Vocabulary(BaseModel):
    """Every name a parse may use. Built from the registry so the grammar
    cannot drift from lights.yaml (D27)."""

    model_config = ConfigDict(frozen=True)

    lights: tuple[str, ...]
    groups: tuple[str, ...]
    scenes: tuple[str, ...]
    colours: tuple[str, ...]
    temperatures: tuple[str, ...]

    @classmethod
    def from_registry(cls, registry: LightsRegistry) -> Vocabulary:
        return cls(
            lights=tuple(registry.lights),
            groups=tuple(registry.groups),
            scenes=tuple(registry.scenes),
            colours=tuple(NAMED_COLOURS),
            temperatures=tuple(TEMPERATURE_WORDS),
        )

    @property
    def targets(self) -> tuple[str, ...]:
        return self.lights + self.groups + (ALL,)


class InvalidIntent(Exception):
    pass


def validate_intent(intent: Intent, vocabulary: Vocabulary) -> Intent:
    match intent.intent:
        case IntentKind.UNKNOWN:
            return intent
        case IntentKind.APPLY_SCENE:
            if intent.scene not in vocabulary.scenes:
                raise InvalidIntent(f"unknown scene {intent.scene!r}")
            if intent.targets or intent.state is not None:
                raise InvalidIntent("apply_scene takes no targets or state")
            return intent
        case IntentKind.QUERY | IntentKind.SET_LIGHT:
            if not intent.targets:
                raise InvalidIntent("targets is required")
            for target in intent.targets:
                if target not in vocabulary.targets:
                    raise InvalidIntent(f"unknown target {target!r}")
            if intent.intent is IntentKind.QUERY:
                return intent
            if intent.state is None or intent.state.empty:
                raise InvalidIntent("state must set power, brightness_pct or color")
            colour = intent.state.color
            if colour is not None and colour not in vocabulary.colours + vocabulary.temperatures:
                raise InvalidIntent(f"unknown colour {colour!r}")
            return intent
