"""Tier 1: deterministic intent parsing (D26). Keyword table plus a little
regex. Sub-millisecond, offline, no API cost; the common utterances must
land here so the lights work with the WAN down."""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from atlas.lights.application.registry import ALL
from atlas.voice.domain.intent import (
    UNKNOWN_INTENT,
    Intent,
    IntentKind,
    InvalidIntent,
    StateWords,
    Vocabulary,
    validate_intent,
)

_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

# Words that carry no meaning of their own; dropped once classified.
FILLER = frozenset(
    [
        "turn",
        "switch",
        "set",
        "make",
        "put",
        "the",
        "a",
        "an",
        "to",
        "at",
        "please",
        "light",
        "my",
        "it",
        "them",
        "and",
        "then",
        "dim",
    ]
)
# Words that mean "every light" rather than naming one. Handled separately
# from FILLER because their presence (or absence) decides the implicit
# target when a state word has no explicit light or group.
ALL_WORDS = frozenset({"everything", "all", "lights"})
POWER_WORDS: dict[str, Literal["on", "off", "toggle"]] = {
    "on": "on",
    "off": "off",
    "toggle": "toggle",
}
COLOUR_ALIASES = {"warmer": "warm", "cooler": "cool"}
QUERY_WORDS = frozenset({"is", "are", "what", "whats"})
SCENE_LEAD = frozenset({"scene", "activate", "run"})
SCENE_NOISE = frozenset({"mode", "lights", "scene", "the"})


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Intent
    partial: bool


def normalise(text: str) -> str:
    """lowercase, punctuation to spaces, number words 0-100 to digits,
    '%' to ' percent ', collapse spaces."""
    lowered = text.lower().replace("%", " percent ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    words = lowered.split()

    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None
        if word in _TENS and nxt in _UNITS and _UNITS[nxt] < 10:
            out.append(str(_TENS[word] + _UNITS[nxt]))
            i += 2
        elif word == "one" and nxt == "hundred":
            out.append("100")
            i += 2
        elif word in _TENS:
            out.append(str(_TENS[word]))
            i += 1
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
            i += 1
        elif word == "hundred":
            out.append("100")
            i += 1
        else:
            out.append(word)
            i += 1
    return " ".join(out)


def match_scene(phrase: str, scenes: Sequence[str]) -> str | None:
    """Normalise, drop noise words, exact match, then a single close fuzzy
    match; two close matches is ambiguous and resolves to None."""
    words = [w for w in normalise(phrase).split() if w not in SCENE_NOISE]
    key = " ".join(words)
    if not key:
        return None
    if key in scenes:
        return key
    close = difflib.get_close_matches(key, list(scenes), n=2, cutoff=0.6)
    return close[0] if len(close) == 1 else None


def _target_aliases(vocabulary: Vocabulary) -> dict[str, str]:
    """Spoken forms -> registry names, longest alias first so multi-word
    forms are matched before any of their shorter substrings."""
    aliases: dict[str, str] = {}
    for name in vocabulary.lights:
        spoken = name.replace("-", " ")
        aliases[spoken] = name
        for digit_word, digit in _UNITS.items():
            if digit < 10 and spoken.endswith(f" {digit}"):
                aliases[spoken[: -len(str(digit))] + digit_word] = name
    for group in vocabulary.groups:
        aliases[group] = group
        aliases[f"{group} lights"] = group
    return dict(sorted(aliases.items(), key=lambda kv: -len(kv[0])))


def _finish(intent: Intent, vocabulary: Vocabulary, *, partial: bool = False) -> ParseResult:
    try:
        return ParseResult(intent=validate_intent(intent, vocabulary), partial=partial)
    except InvalidIntent:
        return ParseResult(intent=UNKNOWN_INTENT, partial=True)


def parse(text: str, vocabulary: Vocabulary) -> ParseResult:
    normalised = normalise(text)
    words = normalised.split()
    if not words:
        return ParseResult(intent=UNKNOWN_INTENT, partial=False)

    # Targets: consume light/group aliases from the text so the remaining
    # words can be scanned for state without target names in the way.
    targets: list[str] = []
    remaining = f" {normalised} "
    for alias, name in _target_aliases(vocabulary).items():
        padded = f" {alias} "
        if padded in remaining:
            targets.append(name)
            remaining = remaining.replace(padded, " ", 1)
    rest = remaining.split()

    power: Literal["on", "off", "toggle"] | None = None
    brightness: int | None = None
    colour: str | None = None
    all_word_seen = False
    is_query = False
    leftover: list[str] = []

    for word in rest:
        if word in POWER_WORDS and power is None:
            power = POWER_WORDS[word]
        elif word.isdigit() and brightness is None and 0 <= int(word) <= 100:
            brightness = int(word)
        elif word == "percent":
            continue
        elif word in COLOUR_ALIASES:
            colour = colour or COLOUR_ALIASES[word]
        elif word in vocabulary.colours or word in vocabulary.temperatures:
            colour = colour or word
        elif word in ALL_WORDS:
            all_word_seen = True
        elif word in QUERY_WORDS:
            is_query = True
        elif word in SCENE_LEAD or word in FILLER:
            continue
        else:
            leftover.append(word)

    has_state = power is not None or brightness is not None or colour is not None

    # "off" is both a scene key and a power word; the scene wins only when
    # it is the whole utterance (no target, no "lights"/"all" either).
    if not targets and not all_word_seen and len(rest) == 1 and rest[0] in vocabulary.scenes:
        return _finish(Intent(intent=IntentKind.APPLY_SCENE, scene=rest[0]), vocabulary)

    if not targets and (not has_state or words[0] in SCENE_LEAD):
        scene = match_scene(" ".join(leftover), vocabulary.scenes)
        if scene is not None:
            return _finish(Intent(intent=IntentKind.APPLY_SCENE, scene=scene), vocabulary)

    if is_query:
        query_targets = tuple(targets) if targets else ((ALL,) if all_word_seen else ())
        if query_targets:
            return _finish(Intent(intent=IntentKind.QUERY, targets=query_targets), vocabulary)

    if has_state:
        final_targets = tuple(targets) if targets else (ALL,)
        intent = Intent(
            intent=IntentKind.SET_LIGHT,
            targets=final_targets,
            state=StateWords(power=power, brightness_pct=brightness, color=colour),
        )
        return _finish(intent, vocabulary, partial=bool(leftover))

    effective_targets = tuple(targets) if targets else ((ALL,) if all_word_seen else ())
    if effective_targets:
        # A target with no state and no scene, or a colour/brightness word
        # with an unplaceable token, is a hint, not an actuation: it must
        # not go through validate_intent (no state means InvalidIntent),
        # it is already partial and never acted on.
        return ParseResult(
            intent=Intent(intent=IntentKind.SET_LIGHT, targets=effective_targets), partial=True
        )

    return ParseResult(intent=UNKNOWN_INTENT, partial=False)
