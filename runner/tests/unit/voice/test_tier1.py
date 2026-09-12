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
        ("what is on", Intent(intent=IntentKind.QUERY, targets=("all",))),
        ("is it on", Intent(intent=IntentKind.QUERY, targets=("all",))),
        ("off", Intent(intent=IntentKind.APPLY_SCENE, scene="off")),
        ("knight mode", Intent(intent=IntentKind.APPLY_SCENE, scene="night")),
        ("turn on the bedroom lamp", _set(("bedroom",), power="on")),
        (
            "turn the lights off in the bedroom",
            _set(("bedroom",), power="off"),
        ),
        ("lights on in the bedroom", _set(("bedroom",), power="on")),
        ("hey atlas turn off the lights", _set(("all",), power="off")),
        ("lights whiter", _set(("all",), color="white")),
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


def test_weak_fuzzy_scene_match_is_partial() -> None:
    """A weak fuzzy match (ratio below 0.8) is a guess: it must not actuate
    on its own, so it comes back as a partial apply_scene hint for tier 2."""
    bright = parse("bright", VOCAB)
    assert bright.intent == Intent(intent=IntentKind.APPLY_SCENE, scene="night")
    assert bright.partial is True

    nite = parse("nite", VOCAB)
    assert nite.intent == Intent(intent=IntentKind.APPLY_SCENE, scene="night")
    assert nite.partial is True

    evenng = parse("evenng", VOCAB)
    assert evenng.intent == Intent(intent=IntentKind.APPLY_SCENE, scene="evening")
    assert evenng.partial is False, "ratio 0.92 is a strong match"

    evening_mode = parse("evening mode", VOCAB)
    assert evening_mode.intent == Intent(intent=IntentKind.APPLY_SCENE, scene="evening")
    assert evening_mode.partial is False, "exact after noise stripping"


def test_query_with_state_word_never_actuates() -> None:
    """A query about brightness must not fall through to a complete
    SET_LIGHT just because a state word was present (D26 fix round 2,
    finding 1)."""
    result = parse("what is the bedroom at 50 percent", VOCAB)
    assert result.intent.intent is IntentKind.QUERY
    assert result.intent.targets == ("bedroom",)


def test_two_letter_scene_near_miss_is_partial() -> None:
    """ratio("of", "off") is 0.8, at the strong cutoff, but a two-letter key
    is never a deliberate scene name on its own (D26 fix round 2, finding
    2)."""
    result = parse("of", VOCAB)
    assert result.intent == Intent(intent=IntentKind.APPLY_SCENE, scene="off")
    assert result.partial is True


@pytest.mark.parametrize(
    "text",
    [
        "lights off in five minutes",  # a time word defers to tier 2
        "ceiling one off and ceiling two on",  # conflicting power words
        "turn the bedroom lights down",  # target only, no usable state
    ],
)
def test_common_phrasings_that_stay_partial(text: str) -> None:
    """The FILLER/TIME_WORDS split (D26 fix round 2, finding 3) must not
    swallow the cases tier 1 genuinely cannot finish alone."""
    result = parse(text, VOCAB)
    assert result.partial is True


def test_hyphenated_group_name_is_speakable() -> None:
    """_target_aliases must dehyphenate group names the same way it does
    light names, or a group like living-room can never be matched."""
    registry = LightsRegistry.from_mapping(
        {"lights": {"a": {"node_id": 1}}, "groups": {"living-room": ["a"]}}
    )
    vocab = Vocabulary.from_registry(registry)
    assert parse("living room off", vocab).intent.targets == ("living-room",)
    assert parse("living room lights off", vocab).intent.targets == ("living-room",)
