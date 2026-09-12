"""Tier 1 parse table (spec 7.2). One row per phrase family; partials and
ambiguous scenes included. Everything here must pass with the network off."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.lights.application.registry import LightsRegistry
from atlas.voice.application.tier1 import match_scene, normalise, parse
from atlas.voice.domain.intent import Intent, IntentKind, StateWords, Vocabulary

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"
VOCAB = Vocabulary.from_registry(LightsRegistry.load(FIXTURE))


def _set(targets: tuple[str, ...], **state: object) -> Intent:
    return Intent(intent=IntentKind.SET_LIGHT, targets=targets, state=StateWords(**state))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("turn off the lights", _set(("all",), power="off")),
        ("Lights on!", _set(("all",), power="on")),
        ("turn on ceiling one", _set(("ceiling-1",), power="on")),
        ("ceiling-3 off", _set(("ceiling-3",), power="off")),
        ("toggle the bedroom", _set(("bedroom",), power="toggle")),
        ("bedroom lights to forty percent", _set(("bedroom",), brightness_pct=40)),
        ("set everything to 100%", _set(("all",), brightness_pct=100)),
        ("dim ceiling two to 5 percent", _set(("ceiling-2",), brightness_pct=5)),
        ("make the lights warm", _set(("all",), color="warm")),
        ("ceiling 4 red", _set(("ceiling-4",), color="red")),
        ("bedroom warmer please", _set(("bedroom",), color="warm")),
        ("turn the lights blue at 30 percent", _set(("all",), brightness_pct=30, color="blue")),
        ("ceiling one and ceiling two off", _set(("ceiling-1", "ceiling-2"), power="off")),
        ("evening", Intent(intent=IntentKind.APPLY_SCENE, scene="evening")),
        ("evening mode", Intent(intent=IntentKind.APPLY_SCENE, scene="evening")),
        ("set the evening lights", Intent(intent=IntentKind.APPLY_SCENE, scene="evening")),
        ("night scene", Intent(intent=IntentKind.APPLY_SCENE, scene="night")),
        ("evenng", Intent(intent=IntentKind.APPLY_SCENE, scene="evening")),
        ("are the lights on", Intent(intent=IntentKind.QUERY, targets=("all",))),
        ("is ceiling three on", Intent(intent=IntentKind.QUERY, targets=("ceiling-3",))),
    ],
)
def test_complete_parses(text: str, expected: Intent) -> None:
    result = parse(text, VOCAB)
    assert result.intent == expected
    assert result.partial is False


@pytest.mark.parametrize(
    "text",
    [
        "ceiling two",  # target, nothing to do
        "a bit dimmer",  # state-ish word with no grammar
        "make it cosy",  # nothing in vocabulary
    ],
)
def test_partials_and_unknowns_never_actuate(text: str) -> None:
    result = parse(text, VOCAB)
    assert result.intent.intent in (IntentKind.UNKNOWN, IntentKind.SET_LIGHT)
    if result.intent.intent is IntentKind.SET_LIGHT:
        assert result.partial is True


def test_partial_carries_the_recognised_target_as_a_hint() -> None:
    result = parse("ceiling two", VOCAB)
    assert result.partial is True
    assert result.intent.targets == ("ceiling-2",)


def test_unrelated_sentence_is_unknown_not_partial() -> None:
    result = parse("what is the weather tomorrow", VOCAB)
    assert result.intent.intent is IntentKind.UNKNOWN
    assert result.partial is False


def test_off_scene_does_not_shadow_the_power_word() -> None:
    """'off' is both a scene key and a power word; power wins when a target
    or 'lights' is present, the scene wins when it is the whole utterance."""
    assert parse("lights off", VOCAB).intent == _set(("all",), power="off")
    assert parse("off", VOCAB).intent == Intent(intent=IntentKind.APPLY_SCENE, scene="off")


def test_normalise() -> None:
    assert normalise("Turn the Lights to Forty-Five percent, please!") == (
        "turn the lights to 45 percent please"
    )
    assert normalise("100%") == "100 percent"


def test_match_scene_is_exact_then_fuzzy_then_ambiguous() -> None:
    scenes = ("evening", "evening-late", "night")
    assert match_scene("evening", scenes) == "evening"
    assert match_scene("nite", scenes) == "night"
    assert match_scene("evenin", scenes) is None, "two close matches: ambiguous"
    assert match_scene("party", scenes) is None
