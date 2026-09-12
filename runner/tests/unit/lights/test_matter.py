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
)
from atlas.lights.domain.model import Light, LightCommand, UnsupportedFeature

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
