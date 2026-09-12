"""Cluster-level maths (spec 4.1). These are the bytes the bulb gets."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.lights.domain.matter import (
    COLOR_CLUSTER,
    LEVEL_CLUSTER,
    ONOFF_CLUSTER,
    capabilities_from,
    decode,
    plan,
    satisfies,
)
from atlas.lights.domain.model import Light, LightCommand, LightState, UnsupportedFeature

LIGHT = Light(name="ceiling-1", node_id=7, endpoint_id=1)
NOW = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

RGBCW_ATTRIBUTES: dict[str, object] = {
    "1/6/0": True,
    "1/8/0": 127,
    "1/768/0": 85,
    "1/768/1": 254,
    "1/768/7": 370,
    "1/768/8": 2,
    "1/768/16395": 153,
    "1/768/16396": 500,
    "1/768/65532": 0x11,
    "0/40/1": "Lightinginside",
    "0/40/3": "E12 RGBCW",
}

ONOFF_ONLY_ATTRIBUTES: dict[str, object] = {"1/6/0": False}


def test_capabilities_from_a_full_colour_bulb() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    assert caps.features.dimming and caps.features.color_temperature and caps.features.color
    assert caps.color_temp_min_k == 2000
    assert caps.color_temp_max_k == 6536


def test_capabilities_from_an_onoff_plug() -> None:
    caps = capabilities_from(LIGHT, ONOFF_ONLY_ATTRIBUTES)
    assert caps.features.dimming is False
    assert caps.features.color is False
    assert caps.features.color_temperature is False


def test_decode_reads_every_relevant_attribute() -> None:
    state = decode(LIGHT, RGBCW_ATTRIBUTES, reachable=True, observed_at=NOW)
    assert state.on is True
    assert state.brightness == 50
    assert state.color_temp_k == 2703
    assert state.hue == 120
    assert state.saturation == 100
    assert state.reachable is True
    assert state.observed_at == NOW


def test_decode_ignores_unknown_and_missing_attributes() -> None:
    state = decode(LIGHT, {"1/6/0": False, "1/9999/0": 1}, reachable=False, observed_at=NOW)
    assert state.on is False
    assert state.brightness is None
    assert state.reachable is False


def test_plan_orders_colour_then_level_then_power_and_sends_power_explicitly() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    commands = plan(LIGHT, caps, LightCommand(brightness=40, color_temp_k=2700, transition_ms=500))
    assert [c.cluster_id for c in commands] == [COLOR_CLUSTER, LEVEL_CLUSTER, ONOFF_CLUSTER]
    assert commands[0].name == "moveToColorTemperature"
    assert commands[0].payload == {
        "colorTemperatureMireds": 370,
        "transitionTime": 5,
        "optionsMask": 0,
        "optionsOverride": 0,
    }
    assert commands[1].name == "moveToLevelWithOnOff"
    assert commands[1].payload["level"] == 102
    assert commands[2].name == "on"
    assert commands[2].payload == {}


def test_plan_off_sends_only_power() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    commands = plan(LIGHT, caps, LightCommand(on=False))
    assert [(c.cluster_id, c.name) for c in commands] == [(ONOFF_CLUSTER, "off")]


def test_plan_brightness_zero_is_off_not_level_zero() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    commands = plan(LIGHT, caps, LightCommand(brightness=0))
    assert [(c.cluster_id, c.name) for c in commands] == [(ONOFF_CLUSTER, "off")]


def test_plan_hue_and_saturation() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    commands = plan(LIGHT, caps, LightCommand(hue=120, saturation=100))
    assert commands[0].name == "moveToHueAndSaturation"
    assert commands[0].payload["hue"] == 85
    assert commands[0].payload["saturation"] == 254
    assert commands[-1].name == "on", "colour without power still switches the bulb on"


def test_plan_clamps_colour_temperature_to_the_bulb() -> None:
    caps = capabilities_from(LIGHT, RGBCW_ATTRIBUTES)
    commands = plan(LIGHT, caps, LightCommand(color_temp_k=1000))
    assert commands[0].payload["colorTemperatureMireds"] == 500


def test_plan_refuses_a_feature_the_bulb_lacks() -> None:
    caps = capabilities_from(LIGHT, ONOFF_ONLY_ATTRIBUTES)
    with pytest.raises(UnsupportedFeature, match="dimming"):
        plan(LIGHT, caps, LightCommand(brightness=50))
    with pytest.raises(UnsupportedFeature, match="color"):
        plan(LIGHT, caps, LightCommand(hue=1, saturation=1))


# --- satisfies() (spec 4.2 fix round 1): confirmation means the reported
# state matches the request, not merely that some attribute changed. ------


def test_satisfies_ignores_fields_the_command_did_not_set() -> None:
    state = LightState(on=True, brightness=None, color_temp_k=None)
    assert satisfies(state, LightCommand(on=True)) is True


def test_satisfies_on_is_exact() -> None:
    assert satisfies(LightState(on=True), LightCommand(on=True)) is True
    assert satisfies(LightState(on=False), LightCommand(on=True)) is False
    assert satisfies(LightState(on=None), LightCommand(on=True)) is False


def test_satisfies_on_false_checks_only_power() -> None:
    # brightness=0 forces on=False on the command; a wildly different
    # brightness in the reported state must not matter.
    command = LightCommand(brightness=0)
    assert command.on is False
    assert satisfies(LightState(on=False, brightness=87), command) is True
    assert satisfies(LightState(on=True, brightness=0), command) is False


def test_satisfies_brightness_tolerance_is_one() -> None:
    command = LightCommand(brightness=40)
    assert satisfies(LightState(on=True, brightness=41), command) is True
    assert satisfies(LightState(on=True, brightness=39), command) is True
    assert satisfies(LightState(on=True, brightness=42), command) is False
    assert satisfies(LightState(on=True, brightness=None), command) is False


def test_satisfies_color_temp_tolerance_is_the_larger_of_2pct_or_60k() -> None:
    # 2% of 2700 is 54, below the 60K floor.
    warm = LightCommand(color_temp_k=2700)
    assert satisfies(LightState(color_temp_k=2760), warm) is True
    assert satisfies(LightState(color_temp_k=2761), warm) is False
    # 2% of 6000 is 120, above the floor.
    cool = LightCommand(color_temp_k=6000)
    assert satisfies(LightState(color_temp_k=6120), cool) is True
    assert satisfies(LightState(color_temp_k=6121), cool) is False
    assert satisfies(LightState(color_temp_k=None), warm) is False


def test_satisfies_hue_tolerance_is_three_degrees_with_wraparound() -> None:
    command = LightCommand(hue=0, saturation=50)
    assert satisfies(LightState(hue=0, saturation=50), command) is True
    assert satisfies(LightState(hue=357, saturation=50), command) is True, "wraps below 0"
    assert satisfies(LightState(hue=356, saturation=50), command) is False
    assert satisfies(LightState(hue=None, saturation=50), command) is False


def test_satisfies_saturation_tolerance_is_two() -> None:
    command = LightCommand(hue=10, saturation=50)
    assert satisfies(LightState(hue=10, saturation=52), command) is True
    assert satisfies(LightState(hue=10, saturation=53), command) is False
    assert satisfies(LightState(hue=10, saturation=None), command) is False
