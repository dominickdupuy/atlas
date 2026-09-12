"""Matter cluster knowledge (spec 4.1). Attribute paths are the controller's
"endpoint/cluster/attribute" strings; command names are matter.js camelCase.

Order in plan() is colour, level, power: a bulb must never flash its old
colour at full brightness on the way to the requested state. Power is always
sent explicitly (D28): level 0 is not off on most firmware.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from atlas.lights.domain.colour import (
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
from atlas.lights.domain.model import (
    ClusterCommand,
    Light,
    LightCapabilities,
    LightCommand,
    LightFeatures,
    LightState,
    UnsupportedFeature,
)

IDENTIFY_CLUSTER = 3
ONOFF_CLUSTER = 6
LEVEL_CLUSTER = 8
BASIC_CLUSTER = 40
COLOR_CLUSTER = 768

RELEVANT_CLUSTERS = (ONOFF_CLUSTER, LEVEL_CLUSTER, COLOR_CLUSTER)
"""What decode() reads and reconciliation re-reads."""

ONOFF_ATTR = 0
LEVEL_ATTR = 0
HUE_ATTR = 0
SATURATION_ATTR = 1
COLOR_TEMP_ATTR = 7
COLOR_MODE_ATTR = 8
COLOR_TEMP_MIN_ATTR = 16395  # ColorTempPhysicalMinMireds (0x400B)
COLOR_TEMP_MAX_ATTR = 16396  # ColorTempPhysicalMaxMireds (0x400C)
FEATURE_MAP_ATTR = 65532
VENDOR_NAME_ATTR = 1
PRODUCT_NAME_ATTR = 3
NODE_LABEL_ATTR = 5

COLOR_FEATURE_HS = 0x1
COLOR_FEATURE_CT = 0x10


def attribute_path(endpoint_id: int, cluster_id: int, attribute_id: int) -> str:
    return f"{endpoint_id}/{cluster_id}/{attribute_id}"


def _int(attributes: Mapping[str, object], path: str) -> int | None:
    value = attributes.get(path)
    if isinstance(value, bool):
        return int(value)
    return value if isinstance(value, int) else None


def _bool(attributes: Mapping[str, object], path: str) -> bool | None:
    value = attributes.get(path)
    return value if isinstance(value, bool) else None


def _has_cluster(attributes: Mapping[str, object], endpoint_id: int, cluster_id: int) -> bool:
    prefix = f"{endpoint_id}/{cluster_id}/"
    return any(path.startswith(prefix) for path in attributes)


def capabilities_from(light: Light, attributes: Mapping[str, object]) -> LightCapabilities:
    endpoint = light.endpoint_id
    feature_map = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, FEATURE_MAP_ATTR))
    has_color_cluster = _has_cluster(attributes, endpoint, COLOR_CLUSTER)
    if feature_map is not None:
        color = bool(feature_map & COLOR_FEATURE_HS)
        color_temperature = bool(feature_map & COLOR_FEATURE_CT)
    else:
        color = has_color_cluster and (
            _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, HUE_ATTR)) is not None
        )
        color_temperature = has_color_cluster and (
            _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, COLOR_TEMP_ATTR)) is not None
        )
    min_mireds = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, COLOR_TEMP_MIN_ATTR))
    max_mireds = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, COLOR_TEMP_MAX_ATTR))
    capabilities = LightCapabilities(
        features=LightFeatures(
            dimming=_has_cluster(attributes, endpoint, LEVEL_CLUSTER),
            color_temperature=color_temperature,
            color=color,
        )
    )
    if min_mireds and max_mireds:
        # Physical MIN mireds is the MAX kelvin, and vice versa.
        capabilities = capabilities.model_copy(
            update={
                "color_temp_min_k": mireds_to_kelvin(max_mireds),
                "color_temp_max_k": mireds_to_kelvin(min_mireds),
            }
        )
    return capabilities


def decode(
    light: Light,
    attributes: Mapping[str, object],
    *,
    reachable: bool,
    observed_at: datetime,
) -> LightState:
    endpoint = light.endpoint_id
    level = _int(attributes, attribute_path(endpoint, LEVEL_CLUSTER, LEVEL_ATTR))
    mireds = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, COLOR_TEMP_ATTR))
    hue = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, HUE_ATTR))
    saturation = _int(attributes, attribute_path(endpoint, COLOR_CLUSTER, SATURATION_ATTR))
    return LightState(
        on=_bool(attributes, attribute_path(endpoint, ONOFF_CLUSTER, ONOFF_ATTR)),
        brightness=level_to_pct(level) if level is not None else None,
        color_temp_k=mireds_to_kelvin(mireds) if mireds else None,
        hue=matter_to_hue_deg(hue) if hue is not None else None,
        saturation=matter_to_sat_pct(saturation) if saturation is not None else None,
        reachable=reachable,
        observed_at=observed_at,
    )


def plan(
    light: Light, capabilities: LightCapabilities, command: LightCommand
) -> list[ClusterCommand]:
    endpoint = light.endpoint_id
    features = capabilities.features
    transition = ms_to_tenths(command.transition_ms)
    level_options = {"transitionTime": transition, "optionsMask": 0, "optionsOverride": 0}
    # ColorControl's effective ExecuteIfOff bit is (Options & ~mask) | (override
    # & mask); 0/0 leaves it false, so a bulb that is off drops the command and
    # comes on later at its old colour. mask=1, override=1 forces it true.
    # moveToLevelWithOnOff needs no such override: the WithOnOff variant
    # always executes regardless of the bulb's on/off state.
    color_options = {"transitionTime": transition, "optionsMask": 1, "optionsOverride": 1}
    commands: list[ClusterCommand] = []

    if command.on is False:
        return [
            ClusterCommand(endpoint_id=endpoint, cluster_id=ONOFF_CLUSTER, name="off", payload={})
        ]

    if command.color_temp_k is not None:
        if not features.color_temperature:
            raise UnsupportedFeature(light.name, "color_temperature")
        kelvin = min(
            max(command.color_temp_k, capabilities.color_temp_min_k),
            capabilities.color_temp_max_k,
        )
        commands.append(
            ClusterCommand(
                endpoint_id=endpoint,
                cluster_id=COLOR_CLUSTER,
                name="moveToColorTemperature",
                payload={"colorTemperatureMireds": kelvin_to_mireds(kelvin), **color_options},
            )
        )
    if command.hue is not None and command.saturation is not None:
        if not features.color:
            raise UnsupportedFeature(light.name, "color")
        commands.append(
            ClusterCommand(
                endpoint_id=endpoint,
                cluster_id=COLOR_CLUSTER,
                name="moveToHueAndSaturation",
                payload={
                    "hue": hue_deg_to_matter(command.hue),
                    "saturation": sat_pct_to_matter(command.saturation),
                    **color_options,
                },
            )
        )
    if command.brightness is not None:
        if not features.dimming:
            raise UnsupportedFeature(light.name, "dimming")
        commands.append(
            ClusterCommand(
                endpoint_id=endpoint,
                cluster_id=LEVEL_CLUSTER,
                name="moveToLevelWithOnOff",
                payload={
                    "level": pct_to_level(command.brightness, minimum_on=True),
                    **level_options,
                },
            )
        )
    # Power last and always explicit: a colour or level command never
    # substitutes for "on".
    commands.append(
        ClusterCommand(endpoint_id=endpoint, cluster_id=ONOFF_CLUSTER, name="on", payload={})
    )
    return commands


def satisfies(state: LightState, command: LightCommand) -> bool:
    """Whether a device-reported state reflects everything a command asked
    for, within measurement rounding tolerance.

    Used to decide when an in-flight apply is confirmed: confirmation means
    the state now matches the request, not merely that some attribute
    changed (a partial confirmation, or a disconnect wake, must not count).
    Fields the command leaves unset are ignored. `on=False` checks only
    power: the rest of a "turn off" command's fields are noise.
    """
    if command.on is False:
        return state.on is False
    if command.on is True and state.on is not True:
        return False
    if command.brightness is not None and (
        state.brightness is None or abs(state.brightness - command.brightness) > 1
    ):
        return False
    if command.color_temp_k is not None:
        tolerance = max(command.color_temp_k * 0.02, 60)
        if state.color_temp_k is None or abs(state.color_temp_k - command.color_temp_k) > tolerance:
            return False
    if command.hue is not None:
        if state.hue is None:
            return False
        diff = abs(state.hue - command.hue) % 360
        if min(diff, 360 - diff) > 3:
            return False
    return not (
        command.saturation is not None
        and (state.saturation is None or abs(state.saturation - command.saturation) > 2)
    )
