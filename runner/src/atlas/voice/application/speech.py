"""Speech composition (D29): one clause, short enough to speak, never a
list of bulbs. The structured result travels beside it for other clients."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from atlas.lights.application.registry import ALL
from atlas.voice.domain.intent import Intent, IntentKind

DIDNT_CATCH = "Didn't catch that."
CONTROLLER_DOWN = "The lights controller is offline."

_NUMBER_WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def speak_name(name: str) -> str:
    if name == ALL:
        return "all lights"
    return name.replace("-", " ")


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]


def _subject(targets: Sequence[str]) -> str:
    if len(targets) == 1:
        return speak_name(targets[0])
    return "lights"


def _failed_suffix(failed: Sequence[str]) -> str:
    if not failed:
        return ""
    if len(failed) == 1:
        return f", {speak_name(failed[0])} didn't respond"
    return f", {len(failed)} lights didn't respond"


_CONTROLLER_UNAVAILABLE = "controller unavailable"


def _mentions_controller_down(value: JsonValue) -> bool:
    """Search a gateway result for the controller-unavailable marker.

    It can surface at the top ("error", from a gateway-level failure or a
    state() query), inside a per-light "errors" map (a set() call where every
    target failed the same way), or nested in a scene's snapshot — so this
    walks the whole JSON value rather than assuming one shape."""
    if isinstance(value, str):
        return _CONTROLLER_UNAVAILABLE in value
    if isinstance(value, dict):
        return any(_mentions_controller_down(v) for v in value.values())
    if isinstance(value, list):
        return any(_mentions_controller_down(v) for v in value)
    return False


def _is_controller_down(result: dict[str, JsonValue] | None) -> bool:
    return result is not None and _mentions_controller_down(result)


def compose(intent: Intent, result: dict[str, JsonValue] | None) -> str:
    if _is_controller_down(result):
        return CONTROLLER_DOWN
    match intent.intent:
        case IntentKind.UNKNOWN:
            return DIDNT_CATCH
        case IntentKind.APPLY_SCENE:
            failed = _names(result, "failed")
            return _capitalise(f"{speak_name(intent.scene or '')} scene{_failed_suffix(failed)}.")
        case IntentKind.QUERY:
            return _compose_query(intent, result)
        case IntentKind.SET_LIGHT:
            return _compose_set_light(intent, result)


def _compose_query(intent: Intent, result: dict[str, JsonValue] | None) -> str:
    lights = result.get("lights") if result else None
    states = dict(lights) if isinstance(lights, dict) else {}
    single = intent.targets[0] if len(intent.targets) == 1 else None
    if single is not None and single != ALL and single in states:
        state = states[single]
        on = state.get("on") if isinstance(state, dict) else None
        return _capitalise(f"{speak_name(single)} is {'on' if on else 'off'}.")
    on_count = sum(1 for s in states.values() if isinstance(s, dict) and s.get("on"))
    off_count = len(states) - on_count
    on_word = _NUMBER_WORDS.get(on_count, str(on_count))
    off_word = _NUMBER_WORDS.get(off_count, str(off_count))
    return _capitalise(f"{on_word} on, {off_word} off.")


def _compose_set_light(intent: Intent, result: dict[str, JsonValue] | None) -> str:
    applied = _names(result, "applied")
    failed = _names(result, "failed")
    subject = _subject(intent.targets)
    if not applied:
        return _capitalise(f"{subject} didn't respond.")
    state = intent.state
    clauses: list[str] = []
    if state is not None:
        if state.power == "toggle":
            clauses.append("toggled")
        elif state.power is not None and state.brightness_pct is None:
            clauses.append(state.power)
        if state.brightness_pct is not None:
            clauses.append(f"at {state.brightness_pct} percent")
        if state.color is not None:
            clauses.append(state.color)
    return _capitalise(f"{subject} {', '.join(clauses)}{_failed_suffix(failed)}.")


def _names(result: dict[str, JsonValue] | None, key: str) -> list[str]:
    if result is None:
        return []
    value = result.get(key)
    return [str(v) for v in value] if isinstance(value, list) else []
