"""Unit conversions (spec 4.1, D28). Every number the bulb sees is made here."""

from __future__ import annotations

import pytest

from atlas.lights.domain.colour import (
    NAMED_COLOURS,
    TEMPERATURE_WORDS,
    hue_deg_to_matter,
    kelvin_to_mireds,
    level_to_pct,
    matter_to_hue_deg,
    matter_to_sat_pct,
    mireds_to_kelvin,
    ms_to_tenths,
    pct_to_level,
    sat_pct_to_matter,
)


@pytest.mark.parametrize(("pct", "level"), [(100, 254), (50, 127), (1, 3), (0, 0)])
def test_pct_to_level(pct: int, level: int) -> None:
    assert pct_to_level(pct) == level


def test_pct_to_level_has_a_floor_of_one_when_on() -> None:
    """A 0.3% request still means 'on, as dim as possible', not off."""
    assert pct_to_level(0, minimum_on=True) == 1


@pytest.mark.parametrize(("level", "pct"), [(254, 100), (127, 50), (1, 0)])
def test_level_to_pct(level: int, pct: int) -> None:
    assert level_to_pct(level) == pct


def test_kelvin_mireds_round_trip() -> None:
    assert kelvin_to_mireds(2700) == 370
    assert mireds_to_kelvin(370) == 2703
    assert kelvin_to_mireds(6500) == 154


def test_hue_and_saturation_scales() -> None:
    assert hue_deg_to_matter(0) == 0
    assert hue_deg_to_matter(360) == 254
    assert hue_deg_to_matter(120) == 85
    assert matter_to_hue_deg(85) == 120
    assert sat_pct_to_matter(100) == 254
    assert sat_pct_to_matter(50) == 127
    assert matter_to_sat_pct(254) == 100


def test_transition_is_tenths_of_a_second() -> None:
    assert ms_to_tenths(500) == 5
    assert ms_to_tenths(0) == 0
    assert ms_to_tenths(49) == 0
    assert ms_to_tenths(50) == 1


def test_colour_tables_are_fixed_and_named_in_the_spec() -> None:
    assert set(NAMED_COLOURS) == {
        "red",
        "orange",
        "amber",
        "yellow",
        "green",
        "teal",
        "blue",
        "purple",
        "pink",
        "white",
    }
    assert TEMPERATURE_WORDS == {"warm": 2700, "neutral": 4000, "cool": 5500}
    for hue, sat in NAMED_COLOURS.values():
        assert 0 <= hue <= 360 and 0 <= sat <= 100
