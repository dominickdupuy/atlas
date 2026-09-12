"""Ports of the lights context (spec 4.2). The controller port is shaped by
the matterjs-server WebSocket API, narrowed to what atlas uses."""

from __future__ import annotations

from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from atlas.lights.domain.matter import (
    BASIC_CLUSTER,
    NODE_LABEL_ATTR,
    PRODUCT_NAME_ATTR,
    VENDOR_NAME_ATTR,
    attribute_path,
)


class MatterNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: int
    available: bool
    attributes: dict[str, JsonValue]

    def _basic(self, attribute_id: int) -> str | None:
        value = self.attributes.get(attribute_path(0, BASIC_CLUSTER, attribute_id))
        return value if isinstance(value, str) else None

    @property
    def vendor_name(self) -> str | None:
        return self._basic(VENDOR_NAME_ATTR)

    @property
    def product_name(self) -> str | None:
        return self._basic(PRODUCT_NAME_ATTR)

    @property
    def node_label(self) -> str | None:
        return self._basic(NODE_LABEL_ATTR)


class ServerInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    min_supported_schema_version: int
    sdk_version: str


class MatterError(Exception):
    """An error_code reply from the controller, verbatim."""

    NAMES: ClassVar[dict[int, str]] = {
        0: "UnknownError",
        1: "NodeCommissionFailed",
        2: "NodeInterviewFailed",
        3: "NodeNotReady",
        4: "NodeNotResolving",
        5: "NodeNotExists",
        6: "VersionMismatch",
        7: "SDKStackError",
        8: "InvalidArguments",
        9: "InvalidCommand",
    }

    def __init__(self, code: int, details: str) -> None:
        super().__init__(f"{self.NAMES.get(code, 'Error')}({code}): {details}")
        self.code = code
        self.details = details


class ControllerUnavailable(Exception):
    """The WebSocket is down. Nothing queues: a light command executed a
    minute late is worse than one refused."""


class ControllerListener(Protocol):
    async def on_connected(self, nodes: list[MatterNode]) -> None: ...

    async def on_disconnected(self) -> None: ...

    async def on_node(self, node: MatterNode) -> None: ...

    async def on_node_removed(self, node_id: int) -> None: ...

    async def on_attribute(self, node_id: int, path: str, value: JsonValue) -> None: ...


class MatterController(Protocol):
    @property
    def connected(self) -> bool: ...

    def subscribe(self, listener: ControllerListener) -> None: ...

    async def nodes(self) -> list[MatterNode]: ...

    async def read(self, node_id: int, attribute_path: str) -> dict[str, JsonValue]: ...

    async def send(
        self, node_id: int, endpoint_id: int, cluster_id: int, name: str, payload: dict[str, int]
    ) -> None: ...

    async def commission_with_code(self, code: str, *, network_only: bool) -> int: ...

    async def remove_node(self, node_id: int) -> None: ...

    async def server_info(self) -> ServerInfo: ...
