"""Lights domain model (spec 4.1). Pure values; no I/O, no clock."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnsupportedFeature(Exception):
    """The light lacks a feature the command needs (a 409 at the edge)."""

    def __init__(self, light: str, feature: str) -> None:
        super().__init__(f"{light} does not support {feature}")
        self.light = light
        self.feature = feature


class Light(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    node_id: int = Field(ge=1)
    endpoint_id: int = Field(default=1, ge=0)


class LightFeatures(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimming: bool
    color_temperature: bool
    color: bool


class LightCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    features: LightFeatures
    color_temp_min_k: int = 2000
    color_temp_max_k: int = 6500


class LightState(BaseModel):
    """What the device last confirmed. Every field is optional because a
    bulb reports clusters piecemeal and an unreachable bulb reports nothing."""

    model_config = ConfigDict(frozen=True)

    on: bool | None = None
    brightness: int | None = None
    color_temp_k: int | None = None
    hue: int | None = None
    saturation: int | None = None
    reachable: bool = True
    observed_at: datetime | None = None


class LightCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    on: bool | None = None
    brightness: int | None = Field(default=None, ge=0, le=100)
    color_temp_k: int | None = Field(default=None, ge=1000, le=10000)
    hue: int | None = Field(default=None, ge=0, le=360)
    saturation: int | None = Field(default=None, ge=0, le=100)
    transition_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _rules(self) -> LightCommand:
        if self.color_temp_k is not None and (self.hue is not None or self.saturation is not None):
            raise ValueError("color_temp_k and hue/saturation are mutually exclusive")
        if (self.hue is None) != (self.saturation is None):
            raise ValueError("hue and saturation must be given together")
        if all(
            value is None
            for value in (self.on, self.brightness, self.color_temp_k, self.hue, self.saturation)
        ):
            raise ValueError("nothing to do: the command sets no field")
        if self.brightness == 0:
            object.__setattr__(self, "on", False)
        elif self.brightness is not None and self.on is None:
            object.__setattr__(self, "on", True)
        return self


class Scene(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    states: dict[str, LightCommand]


class ClusterCommand(BaseModel):
    """One Matter command the controller sends, in the order planned."""

    model_config = ConfigDict(frozen=True)

    endpoint_id: int
    cluster_id: int
    name: str
    payload: dict[str, int]
