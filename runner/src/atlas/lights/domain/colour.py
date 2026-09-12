"""Colour and unit maths (D28). The parser hands over names; the numbers the
bulb sees are all made here, and nowhere else."""

from __future__ import annotations

MATTER_MAX = 254
"""OnOff/LevelControl/ColorControl use 0..254 for level, hue and saturation."""

# name -> (hue degrees 0-360, saturation percent 0-100). Fixed on purpose:
# "red" is the same red every time, whichever tier named it.
NAMED_COLOURS: dict[str, tuple[int, int]] = {
    "red": (0, 100),
    "orange": (30, 100),
    "amber": (45, 100),
    "yellow": (60, 100),
    "green": (120, 100),
    "teal": (180, 100),
    "blue": (240, 100),
    "purple": (275, 100),
    "pink": (330, 60),
    "white": (0, 0),
}

# Colour *temperature* words -> kelvin. A different cluster command from
# named colours, despite sounding like the same request.
TEMPERATURE_WORDS: dict[str, int] = {"warm": 2700, "neutral": 4000, "cool": 5500}


def pct_to_level(pct: int, *, minimum_on: bool = False) -> int:
    level = round(pct / 100 * MATTER_MAX)
    if minimum_on:
        return max(1, level)
    return level


def level_to_pct(level: int) -> int:
    return round(level / MATTER_MAX * 100)


def kelvin_to_mireds(kelvin: int) -> int:
    return round(1_000_000 / kelvin)


def mireds_to_kelvin(mireds: int) -> int:
    return round(1_000_000 / mireds)


def hue_deg_to_matter(degrees: int) -> int:
    return round(degrees / 360 * MATTER_MAX)


def matter_to_hue_deg(value: int) -> int:
    return round(value / MATTER_MAX * 360)


def sat_pct_to_matter(pct: int) -> int:
    return round(pct / 100 * MATTER_MAX)


def matter_to_sat_pct(value: int) -> int:
    return round(value / MATTER_MAX * 100)


def ms_to_tenths(milliseconds: int) -> int:
    return (milliseconds + 50) // 100
