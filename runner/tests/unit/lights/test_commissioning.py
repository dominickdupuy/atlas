from __future__ import annotations

from pathlib import Path

from atlas.lights.application.commissioning import commission, identify, list_nodes, remove
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.infrastructure.stub_controller import StubMatterController

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"


async def test_list_nodes_joins_the_registry_names() -> None:
    stub = StubMatterController()
    summaries = await list_nodes(stub, LightsRegistry.load(FIXTURE))
    assert [s.node_id for s in summaries] == [1, 2, 3, 4]
    assert summaries[0].name == "ceiling-1"
    assert summaries[0].product == "E12 RGBCW"
    assert summaries[0].available is True


async def test_commission_is_network_only() -> None:
    stub = StubMatterController()
    node_id = await commission(stub, "1234-567-8901")
    assert node_id == 5
    assert stub.commissioned == ["12345678901"], "dashes and spaces are stripped"


async def test_identify_blinks_via_the_identify_cluster() -> None:
    stub = StubMatterController()
    await identify(stub, 2, seconds=5)
    assert stub.sent == [(2, 1, 3, "identify", {"identifyTime": 5})]


async def test_remove() -> None:
    stub = StubMatterController()
    await remove(stub, 4)
    assert stub.removed == [4]
