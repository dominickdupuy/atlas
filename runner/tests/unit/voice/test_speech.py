from __future__ import annotations

from pydantic import JsonValue

from atlas.voice.application.speech import CONTROLLER_DOWN, DIDNT_CATCH, compose, speak_name
from atlas.voice.domain.intent import UNKNOWN_INTENT, Intent, IntentKind, StateWords


def _set(*targets: str, **state: object) -> Intent:
    return Intent(intent=IntentKind.SET_LIGHT, targets=targets, state=StateWords(**state))  # type: ignore[arg-type]


def test_speak_name() -> None:
    assert speak_name("ceiling-1") == "ceiling 1"
    assert speak_name("all") == "all lights"
    assert speak_name("bedroom") == "bedroom"


def test_set_light_clauses() -> None:
    ok: dict[str, JsonValue] = {"applied": ["ceiling-1"], "failed": []}
    assert compose(_set("ceiling-1", power="on"), ok) == "Ceiling 1 on."
    warm_result: dict[str, JsonValue] = {"applied": ["a", "b"], "failed": []}
    assert (
        compose(_set("bedroom", brightness_pct=40, color="warm"), warm_result)
        == "Bedroom at 40 percent, warm."
    )
    assert compose(_set("all", power="off"), {"applied": ["a"], "failed": []}) == "All lights off."
    toggle_result: dict[str, JsonValue] = {"applied": ["ceiling-1", "ceiling-2"], "failed": []}
    assert (
        compose(_set("ceiling-1", "ceiling-2", power="toggle"), toggle_result) == "Lights toggled."
    )


def test_partial_and_total_failure_name_the_bulb() -> None:
    assert (
        compose(_set("bedroom", power="off"), {"applied": ["ceiling-1"], "failed": ["ceiling-3"]})
        == "Bedroom off, ceiling 3 didn't respond."
    )
    assert (
        compose(_set("ceiling-2", power="on"), {"applied": [], "failed": ["ceiling-2"]})
        == "Ceiling 2 didn't respond."
    )


def test_scene_query_unknown_and_controller_down() -> None:
    scene = Intent(intent=IntentKind.APPLY_SCENE, scene="evening")
    assert compose(scene, {"scene": "evening", "applied": ["a"], "failed": []}) == "Evening scene."
    assert (
        compose(scene, {"scene": "evening", "applied": ["a"], "failed": ["ceiling-4"]})
        == "Evening scene, ceiling 4 didn't respond."
    )
    query = Intent(intent=IntentKind.QUERY, targets=("ceiling-1",))
    assert compose(query, {"lights": {"ceiling-1": {"on": True}}}) == "Ceiling 1 is on."
    several = Intent(intent=IntentKind.QUERY, targets=("all",))
    assert (
        compose(several, {"lights": {"a": {"on": True}, "b": {"on": False}, "c": {"on": True}}})
        == "Two on, one off."
    )
    assert compose(UNKNOWN_INTENT, None) == DIDNT_CATCH
    assert compose(_set("all", power="on"), {"error": "controller unavailable"}) == CONTROLLER_DOWN
