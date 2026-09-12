"""The D26 contract, and the validation that guards every actuation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.lights.application.registry import LightsRegistry
from atlas.voice.domain.intent import (
    UNKNOWN_INTENT,
    Intent,
    IntentKind,
    InvalidIntent,
    StateWords,
    Vocabulary,
    validate_intent,
)

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"
VOCAB = Vocabulary.from_registry(LightsRegistry.load(FIXTURE))


def test_vocabulary_from_registry() -> None:
    assert VOCAB.lights == ("ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4")
    assert VOCAB.groups == ("bedroom",)
    assert set(VOCAB.scenes) == {"evening", "night", "off"}
    assert "red" in VOCAB.colours and "warm" in VOCAB.temperatures
    assert VOCAB.targets[-1] == "all"


def test_set_light_valid() -> None:
    intent = Intent(
        intent=IntentKind.SET_LIGHT, targets=("bedroom",), state=StateWords(brightness_pct=40)
    )
    assert validate_intent(intent, VOCAB) == intent


@pytest.mark.parametrize(
    ("intent", "field"),
    [
        (
            Intent(intent=IntentKind.SET_LIGHT, targets=(), state=StateWords(power="on")),
            "targets",
        ),
        (
            Intent(
                intent=IntentKind.SET_LIGHT,
                targets=("kitchen",),
                state=StateWords(power="on"),
            ),
            "kitchen",
        ),
        (Intent(intent=IntentKind.SET_LIGHT, targets=("all",), state=StateWords()), "state"),
        (
            Intent(
                intent=IntentKind.SET_LIGHT,
                targets=("all",),
                state=StateWords(color="plaid"),
            ),
            "plaid",
        ),
        (Intent(intent=IntentKind.APPLY_SCENE, scene="party"), "party"),
        (Intent(intent=IntentKind.APPLY_SCENE, scene="evening", targets=("all",)), "targets"),
        (Intent(intent=IntentKind.QUERY, targets=()), "targets"),
    ],
)
def test_invalid_intents_name_the_problem(intent: Intent, field: str) -> None:
    with pytest.raises(InvalidIntent, match=field):
        validate_intent(intent, VOCAB)


def test_unknown_needs_nothing() -> None:
    assert validate_intent(UNKNOWN_INTENT, VOCAB) is UNKNOWN_INTENT


def test_contract_rejects_extra_fields_and_bad_ranges() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate(
            {
                "intent": "set_light",
                "targets": ["all"],
                "state": {"power": "on"},
                "extra": 1,
            }
        )
    with pytest.raises(ValidationError):
        StateWords(brightness_pct=101)
    with pytest.raises(ValidationError):
        StateWords(power="dim")  # type: ignore[arg-type]


def test_json_round_trip_is_the_llm_contract() -> None:
    raw = '{"intent": "apply_scene", "scene": "night"}'
    intent = Intent.model_validate_json(raw)
    assert intent.intent is IntentKind.APPLY_SCENE
    assert intent.model_dump(mode="json", exclude_none=True) == {
        "intent": "apply_scene",
        "targets": [],
        "scene": "night",
    }
