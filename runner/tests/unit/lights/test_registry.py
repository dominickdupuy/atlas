"""lights.yaml -> LightsRegistry (spec 4.4). Bad files fail at load, loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.lights.application.registry import LightsRegistry, RegistryError, UnknownTarget

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"


def test_loads_the_fixture() -> None:
    registry = LightsRegistry.load(FIXTURE)
    assert list(registry.lights) == ["ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4"]
    assert registry.lights["ceiling-3"].node_id == 3
    assert registry.groups["bedroom"] == ("ceiling-1", "ceiling-2", "ceiling-3", "ceiling-4")
    assert set(registry.scenes) == {"evening", "night", "off"}


def test_scene_groups_expand_to_lights() -> None:
    registry = LightsRegistry.load(FIXTURE)
    evening = registry.scenes["evening"]
    assert set(evening.states) == set(registry.lights)
    assert evening.states["ceiling-4"].brightness == 40
    assert evening.states["ceiling-4"].color_temp_k == 2700


def test_resolve_light_group_and_all() -> None:
    registry = LightsRegistry.load(FIXTURE)
    assert registry.resolve("ceiling-2") == ["ceiling-2"]
    assert registry.resolve("bedroom") == list(registry.lights)
    assert registry.resolve("all") == list(registry.lights)
    with pytest.raises(UnknownTarget):
        registry.resolve("kitchen")


def test_by_node() -> None:
    registry = LightsRegistry.load(FIXTURE)
    assert registry.by_node(2) is not None
    assert registry.by_node(2).name == "ceiling-2"  # type: ignore[union-attr]
    assert registry.by_node(99) is None


def test_unknown_light_in_scene_names_the_key() -> None:
    raw = {
        "lights": {"a": {"node_id": 1}},
        "scenes": {"x": {"nope": {"on": True}}},
    }
    with pytest.raises(RegistryError, match=r"scene 'x'.*'nope'"):
        LightsRegistry.from_mapping(raw)


def test_duplicate_node_id_is_rejected() -> None:
    raw = {"lights": {"a": {"node_id": 1}, "b": {"node_id": 1}}}
    with pytest.raises(RegistryError, match="node_id 1"):
        LightsRegistry.from_mapping(raw)


def test_group_name_may_not_shadow_a_light_or_all() -> None:
    with pytest.raises(RegistryError, match="'all'"):
        LightsRegistry.from_mapping({"lights": {"a": {"node_id": 1}}, "groups": {"all": ["a"]}})
    with pytest.raises(RegistryError, match="'a'"):
        LightsRegistry.from_mapping({"lights": {"a": {"node_id": 1}}, "groups": {"a": ["a"]}})


def test_bad_command_in_scene_is_a_registry_error() -> None:
    raw = {"lights": {"a": {"node_id": 1}}, "scenes": {"x": {"a": {"brightness": 500}}}}
    with pytest.raises(RegistryError, match="scene 'x'"):
        LightsRegistry.from_mapping(raw)


def test_missing_file_is_a_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="not found"):
        LightsRegistry.load(tmp_path / "nope.yaml")


def test_empty_registry_is_allowed(tmp_path: Path) -> None:
    """Before commissioning the file has no lights yet; the service must
    still start so the CLI can talk to the controller."""
    path = tmp_path / "lights.yaml"
    path.write_text("lights: {}\n", encoding="utf-8")
    registry = LightsRegistry.load(path)
    assert registry.lights == {}
    assert registry.resolve("all") == []
