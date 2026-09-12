"""LightCommand validation (spec 4.1): the one place ranges are checked."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.lights.domain.model import LightCommand


def test_brightness_zero_means_off() -> None:
    command = LightCommand(brightness=0)
    assert command.on is False


def test_brightness_implies_on_unless_told_otherwise() -> None:
    assert LightCommand(brightness=40).on is True
    assert LightCommand(brightness=40, on=False).on is False


def test_colour_temperature_and_hue_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        LightCommand(color_temp_k=2700, hue=30, saturation=80)


def test_hue_requires_saturation_and_vice_versa() -> None:
    with pytest.raises(ValidationError, match="together"):
        LightCommand(hue=30)
    with pytest.raises(ValidationError, match="together"):
        LightCommand(saturation=30)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"brightness": 101},
        {"brightness": -1},
        {"color_temp_k": 999},
        {"hue": 361, "saturation": 10},
        {"saturation": 101, "hue": 10},
        {"transition_ms": -1},
    ],
)
def test_ranges(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        LightCommand(**kwargs)  # type: ignore[arg-type]


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ValidationError, match="nothing to do"):
        LightCommand()
