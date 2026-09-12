"""The stub is the dev profile's controller and the tests' double; both
must see the same behaviour."""

from __future__ import annotations

from pydantic import JsonValue

from atlas.lights.application.ports import MatterNode
from atlas.lights.infrastructure.stub_controller import StubMatterController, four_bulbs


class _Recorder:
    def __init__(self) -> None:
        self.attributes: list[tuple[int, str, JsonValue]] = []
        self.connected: list[list[MatterNode]] = []

    async def on_connected(self, nodes: list[MatterNode]) -> None:
        self.connected.append(nodes)

    async def on_disconnected(self) -> None:
        return None

    async def on_node(self, node: MatterNode) -> None:
        return None

    async def on_node_removed(self, node_id: int) -> None:
        return None

    async def on_attribute(self, node_id: int, path: str, value: JsonValue) -> None:
        self.attributes.append((node_id, path, value))


async def test_four_bulbs_are_full_colour_and_available() -> None:
    nodes = four_bulbs()
    assert [n.node_id for n in nodes] == [1, 2, 3, 4]
    assert all(n.available for n in nodes)
    assert nodes[0].attributes["1/768/65532"] == 0x11
    assert nodes[0].product_name == "E12 RGBCW"


async def test_send_echoes_the_resulting_attribute_like_a_real_bulb() -> None:
    stub = StubMatterController()
    recorder = _Recorder()
    stub.subscribe(recorder)
    await stub.send(1, 1, 6, "off", {})
    assert stub.sent == [(1, 1, 6, "off", {})]
    assert (1, "1/6/0", False) in recorder.attributes
    assert (await stub.read(1, "1/6/0")) == {"1/6/0": False}


async def test_level_and_colour_commands_update_their_attributes() -> None:
    stub = StubMatterController()
    await stub.send(2, 1, 8, "moveToLevelWithOnOff", {"level": 102, "transitionTime": 0})
    await stub.send(2, 1, 768, "moveToColorTemperature", {"colorTemperatureMireds": 370})
    await stub.send(2, 1, 768, "moveToHueAndSaturation", {"hue": 85, "saturation": 254})
    node = next(n for n in await stub.nodes() if n.node_id == 2)
    assert node.attributes["1/8/0"] == 102
    assert node.attributes["1/6/0"] is True, "moveToLevelWithOnOff switches the bulb on"
    assert node.attributes["1/768/7"] == 370
    assert node.attributes["1/768/0"] == 85


async def test_commission_adds_a_node_and_remove_drops_it() -> None:
    stub = StubMatterController()
    node_id = await stub.commission_with_code("12345678901", network_only=True)
    assert node_id == 5
    assert stub.commissioned == ["12345678901"]
    await stub.remove_node(5)
    assert stub.removed == [5]
    assert all(n.node_id != 5 for n in await stub.nodes())


async def test_server_info_is_inside_the_supported_window() -> None:
    info = await StubMatterController().server_info()
    assert 11 <= info.schema_version <= 13
