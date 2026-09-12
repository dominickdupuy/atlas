"""LightsRegistry: lights.yaml -> names, node IDs, groups, scenes (spec 4.4, D27).

Validated at load like jobs/ is: a bad file fails at boot with the key
named, not at 11pm when someone says "evening".
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from atlas.lights.domain.model import Light, LightCommand, Scene

ALL = "all"


class RegistryError(Exception):
    pass


class UnknownTarget(Exception):
    def __init__(self, target: str) -> None:
        super().__init__(f"unknown light, group or scene target {target!r}")
        self.target = target


class LightsRegistry:
    def __init__(
        self,
        lights: dict[str, Light],
        groups: dict[str, tuple[str, ...]],
        scenes: dict[str, Scene],
    ) -> None:
        self.lights = lights
        self.groups = groups
        self.scenes = scenes
        self._by_node = {light.node_id: light for light in lights.values()}

    @classmethod
    def load(cls, path: Path) -> LightsRegistry:
        if not path.is_file():
            raise RegistryError(f"lights file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise RegistryError(f"{path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise RegistryError(f"{path}: top level must be a mapping")
        try:
            return cls.from_mapping(raw)
        except RegistryError as exc:
            raise RegistryError(f"{path}: {exc}") from exc

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> LightsRegistry:
        lights = cls._lights(raw.get("lights") or {})
        groups = cls._groups(raw.get("groups") or {}, lights)
        scenes = cls._scenes(raw.get("scenes") or {}, lights, groups)
        return cls(lights, groups, scenes)

    @staticmethod
    def _lights(section: object) -> dict[str, Light]:
        if not isinstance(section, Mapping):
            raise RegistryError("'lights' must be a mapping of name -> {node_id, endpoint_id}")
        lights: dict[str, Light] = {}
        seen_nodes: dict[int, str] = {}
        for name, spec in section.items():
            if not isinstance(spec, Mapping):
                raise RegistryError(f"light {name!r} must be a mapping")
            try:
                light = Light(name=str(name), **{str(k): v for k, v in spec.items()})
            except ValidationError as exc:
                raise RegistryError(f"light {name!r}: {exc}") from exc
            if light.node_id in seen_nodes:
                raise RegistryError(
                    f"node_id {light.node_id} is used by both {seen_nodes[light.node_id]!r} "
                    f"and {light.name!r}"
                )
            seen_nodes[light.node_id] = light.name
            lights[light.name] = light
        return lights

    @staticmethod
    def _groups(section: object, lights: Mapping[str, Light]) -> dict[str, tuple[str, ...]]:
        if not isinstance(section, Mapping):
            raise RegistryError("'groups' must be a mapping of name -> [light names]")
        groups: dict[str, tuple[str, ...]] = {}
        for name, members in section.items():
            key = str(name)
            if key == ALL or key in lights:
                raise RegistryError(f"group {key!r} shadows a light name or {ALL!r}")
            if not isinstance(members, list) or not members:
                raise RegistryError(f"group {key!r} must be a non-empty list")
            for member in members:
                if member not in lights:
                    raise RegistryError(f"group {key!r} names unknown light {member!r}")
            groups[key] = tuple(str(m) for m in members)
        return groups

    @classmethod
    def _scenes(
        cls, section: object, lights: Mapping[str, Light], groups: Mapping[str, tuple[str, ...]]
    ) -> dict[str, Scene]:
        if not isinstance(section, Mapping):
            raise RegistryError("'scenes' must be a mapping of name -> {target: command}")
        scenes: dict[str, Scene] = {}
        for name, targets in section.items():
            key = str(name)
            if not isinstance(targets, Mapping):
                raise RegistryError(f"scene {key!r} must be a mapping of target -> command")
            states: dict[str, LightCommand] = {}
            for target, spec in targets.items():
                target_key = str(target)
                if target_key == ALL:
                    members: tuple[str, ...] = tuple(lights)
                elif target_key in groups:
                    members = groups[target_key]
                elif target_key in lights:
                    members = (target_key,)
                else:
                    raise RegistryError(f"scene {key!r} names unknown target {target_key!r}")
                if not isinstance(spec, Mapping):
                    raise RegistryError(f"scene {key!r} target {target_key!r} must be a mapping")
                try:
                    command = LightCommand(**{str(k): v for k, v in spec.items()})
                except ValidationError as exc:
                    raise RegistryError(f"scene {key!r} target {target_key!r}: {exc}") from exc
                for member in members:
                    states[member] = command
            try:
                scenes[key] = Scene(name=key, states=states)
            except ValidationError as exc:
                raise RegistryError(f"scene {key!r}: {exc}") from exc
        return scenes

    def resolve(self, target: str) -> list[str]:
        if target == ALL:
            return list(self.lights)
        if target in self.groups:
            return list(self.groups[target])
        if target in self.lights:
            return [target]
        raise UnknownTarget(target)

    def by_node(self, node_id: int) -> Light | None:
        return self._by_node.get(node_id)
