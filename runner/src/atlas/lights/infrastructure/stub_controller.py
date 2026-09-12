"""StubMatterController: four RGBCW bulbs in memory (ATLAS_MATTER_WS_URL=stub).

A command updates the stub's own attributes and then reports them back
through the listener, the way a real bulb confirms over the subscription.
The service therefore exercises the same "wait for the echo" path in dev
and in tests as it does against hardware.
"""

from __future__ import annotations

from pydantic import JsonValue

from atlas.lights.application.ports import ControllerListener, MatterNode, ServerInfo
from atlas.lights.domain.matter import COLOR_CLUSTER, LEVEL_CLUSTER, ONOFF_CLUSTER

_RGBCW: dict[str, JsonValue] = {
    "0/40/1": "Lightinginside",
    "0/40/3": "E12 RGBCW",
    "1/6/0": False,
    "1/8/0": 254,
    "1/768/0": 0,
    "1/768/1": 0,
    "1/768/7": 370,
    "1/768/8": 2,
    "1/768/16395": 153,
    "1/768/16396": 500,
    "1/768/65532": 0x11,
}


def four_bulbs() -> list[MatterNode]:
    return [
        MatterNode(node_id=node_id, available=True, attributes=dict(_RGBCW))
        for node_id in (1, 2, 3, 4)
    ]


class StubMatterController:
    def __init__(self, nodes: list[MatterNode] | None = None) -> None:
        self._nodes: dict[int, MatterNode] = {
            node.node_id: node for node in (nodes if nodes is not None else four_bulbs())
        }
        self._listeners: list[ControllerListener] = []
        self.connected = True
        self.sent: list[tuple[int, int, int, str, dict[str, int]]] = []
        self.removed: list[int] = []
        self.commissioned: list[str] = []

    def subscribe(self, listener: ControllerListener) -> None:
        self._listeners.append(listener)

    async def nodes(self) -> list[MatterNode]:
        return list(self._nodes.values())

    async def read(self, node_id: int, attribute_path: str) -> dict[str, JsonValue]:
        node = self._nodes[node_id]
        if attribute_path.endswith("/*/*"):
            endpoint = attribute_path.split("/")[0]
            return {p: v for p, v in node.attributes.items() if p.startswith(f"{endpoint}/")}
        return {attribute_path: node.attributes[attribute_path]}

    async def emit_attribute(self, node_id: int, path: str, value: JsonValue) -> None:
        node = self._nodes[node_id]
        attributes = dict(node.attributes)
        attributes[path] = value
        self._nodes[node_id] = node.model_copy(update={"attributes": attributes})
        for listener in self._listeners:
            await listener.on_attribute(node_id, path, value)

    async def send(
        self, node_id: int, endpoint_id: int, cluster_id: int, name: str, payload: dict[str, int]
    ) -> None:
        self.sent.append((node_id, endpoint_id, cluster_id, name, payload))
        base = f"{endpoint_id}"
        if cluster_id == ONOFF_CLUSTER:
            if name == "toggle":
                current = self._nodes[node_id].attributes.get(f"{base}/6/0")
                await self.emit_attribute(node_id, f"{base}/6/0", not current)
            else:
                await self.emit_attribute(node_id, f"{base}/6/0", name == "on")
        elif cluster_id == LEVEL_CLUSTER:
            await self.emit_attribute(node_id, f"{base}/8/0", payload["level"])
            if name.endswith("WithOnOff"):
                await self.emit_attribute(node_id, f"{base}/6/0", payload["level"] > 0)
        elif cluster_id == COLOR_CLUSTER:
            # Honour Matter's ExecuteIfOff semantics: with the bulb off, the
            # command only takes effect if optionsOverride & optionsMask & 1.
            # 0/0 leaves it false, matching a real bulb that drops the
            # command silently; it is still recorded in `.sent` above.
            is_off = self._nodes[node_id].attributes.get(f"{base}/6/0") is False
            mask = payload.get("optionsMask", 0)
            override = payload.get("optionsOverride", 0)
            execute_if_off = bool(override & mask & 1)
            if is_off and not execute_if_off:
                return
            if name == "moveToColorTemperature":
                color_temp = payload["colorTemperatureMireds"]
                await self.emit_attribute(node_id, f"{base}/768/7", color_temp)
                await self.emit_attribute(node_id, f"{base}/768/8", 2)
            elif name == "moveToHueAndSaturation":
                await self.emit_attribute(node_id, f"{base}/768/0", payload["hue"])
                await self.emit_attribute(node_id, f"{base}/768/1", payload["saturation"])
                await self.emit_attribute(node_id, f"{base}/768/8", 0)

    async def commission_with_code(self, code: str, *, network_only: bool) -> int:
        self.commissioned.append(code)
        node_id = max(self._nodes, default=0) + 1
        node = MatterNode(node_id=node_id, available=True, attributes=dict(_RGBCW))
        self._nodes[node_id] = node
        for listener in self._listeners:
            await listener.on_node(node)
        return node_id

    async def remove_node(self, node_id: int) -> None:
        self.removed.append(node_id)
        self._nodes.pop(node_id, None)
        for listener in self._listeners:
            await listener.on_node_removed(node_id)

    async def server_info(self) -> ServerInfo:
        return ServerInfo(schema_version=13, min_supported_schema_version=11, sdk_version="stub")
