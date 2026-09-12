"""MatterWsClient: the runner's long-lived connection to matterjs-server.

Shape borrowed from the MQTT bus adapter: a connection-owning `run()` loop
started as a lifespan task, exponential backoff with jitter, and injectable
`connect` and `sleep` so the failure paths are tested without a server.

Protocol (docs/websockets_api.md upstream): the server speaks first with
`server_info`; requests carry a message_id that the reply echoes; events
arrive unsolicited after `start_listening`. Node IDs are parsed by `json`,
which keeps big integers exact; nothing here goes through a float.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from pydantic import JsonValue

from atlas.lights.application.ports import (
    ControllerListener,
    ControllerUnavailable,
    MatterError,
    MatterNode,
    ServerInfo,
)
from atlas.telemetry.infrastructure.mqtt_bus import ReconnectBackoff

logger = logging.getLogger(__name__)

SCHEMA_MIN = 11
SCHEMA_MAX = 13


class MatterSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str: ...


ConnectFactory = Callable[[str], AbstractAsyncContextManager[MatterSocket]]
Sleeper = Callable[[float], Awaitable[None]]


class SchemaMismatch(Exception):
    def __init__(self, version: int) -> None:
        super().__init__(
            f"controller schema {version} outside supported window [{SCHEMA_MIN}, {SCHEMA_MAX}]"
        )
        self.version = version


def _node_from(data: dict[str, Any]) -> MatterNode:
    return MatterNode(
        node_id=int(data["node_id"]),
        available=bool(data.get("available", False)),
        attributes=dict(data.get("attributes") or {}),
    )


@asynccontextmanager
async def _websockets_connect(url: str) -> AsyncIterator[MatterSocket]:
    from websockets.asyncio.client import connect

    async with connect(url, max_size=None) as connection:

        class _Adapter:
            async def send(self, message: str) -> None:
                await connection.send(message)

            async def recv(self) -> str:
                message = await connection.recv()
                return message if isinstance(message, str) else message.decode()

        yield _Adapter()


class MatterWsClient:
    def __init__(
        self,
        url: str,
        *,
        connect: ConnectFactory | None = None,
        sleep: Sleeper = asyncio.sleep,
        backoff: ReconnectBackoff | None = None,
        request_timeout: float = 10.0,
        commission_timeout: float = 180.0,
        bootstrap_timeout: float = 60.0,
    ) -> None:
        self._url = url
        self._connect: ConnectFactory = connect or _websockets_connect
        self._sleep = sleep
        self._backoff = backoff or ReconnectBackoff()
        self._request_timeout = request_timeout
        self._commission_timeout = commission_timeout
        self._bootstrap_timeout = bootstrap_timeout
        self._socket: MatterSocket | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._listeners: list[ControllerListener] = []
        self._nodes: dict[int, MatterNode] = {}
        self._server_info: ServerInfo | None = None
        self._last_schema_mismatch: int | None = None

    # --- MatterController ----------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def subscribe(self, listener: ControllerListener) -> None:
        self._listeners.append(listener)

    async def nodes(self) -> list[MatterNode]:
        self._require_connected()
        return list(self._nodes.values())

    async def read(self, node_id: int, attribute_path: str) -> dict[str, JsonValue]:
        result = await self._request(
            "read_attribute", {"node_id": node_id, "attribute_path": attribute_path}
        )
        if not isinstance(result, dict):
            logger.warning(
                "read_attribute %s/%s returned %s, expected a path->value dict",
                node_id,
                attribute_path,
                type(result).__name__,
            )
            return {}
        return dict(result)

    async def send(
        self, node_id: int, endpoint_id: int, cluster_id: int, name: str, payload: dict[str, int]
    ) -> None:
        await self._request(
            "device_command",
            {
                "node_id": node_id,
                "endpoint_id": endpoint_id,
                "cluster_id": cluster_id,
                "command_name": name,
                "payload": payload,
            },
        )

    async def commission_with_code(self, code: str, *, network_only: bool) -> int:
        result = await self._request(
            "commission_with_code",
            {"code": code, "network_only": network_only},
            timeout=self._commission_timeout,
        )
        if not isinstance(result, dict) or "node_id" not in result:
            raise MatterError(0, f"unexpected commission reply: {result!r}")
        node = _node_from(result)
        self._nodes[node.node_id] = node
        return node.node_id

    async def remove_node(self, node_id: int) -> None:
        await self._request("remove_node", {"node_id": node_id}, timeout=self._commission_timeout)

    async def server_info(self) -> ServerInfo:
        # Cached so callers can read the last handshake while connected;
        # nothing compares it against a later reconnect. It must never be
        # handed out while disconnected.
        if self._socket is None or self._server_info is None:
            raise ControllerUnavailable("not connected")
        return self._server_info

    # --- the loop --------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                async with self._connect(self._url) as socket:
                    await self._session(socket)
            except SchemaMismatch as exc:
                # A schema mismatch is refused, not fatal: the controller
                # may be upgraded without a runner restart, so we keep
                # retrying on the ordinary backoff. Only log at error level
                # once per mismatched version, so a stuck controller does
                # not spam the log on every retry.
                if exc.version != self._last_schema_mismatch:
                    logger.error("%s; refusing to operate", exc)
                    self._last_schema_mismatch = exc.version
                else:
                    logger.debug("%s; still refusing to operate", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("matter controller connection lost (%s)", exc)
            finally:
                await self._teardown()
            delay = self._backoff.next_delay()
            logger.info("matter controller retry %d in %.1fs", self._backoff.attempts, delay)
            await self._sleep(delay)

    async def _session(self, socket: MatterSocket) -> None:
        first = json.loads(await socket.recv())
        version = int(first.get("schema_version", -1))
        if not SCHEMA_MIN <= version <= SCHEMA_MAX:
            raise SchemaMismatch(version)
        self._server_info = ServerInfo(
            schema_version=version,
            min_supported_schema_version=int(first.get("min_supported_schema_version", version)),
            sdk_version=str(first.get("sdk_version", "")),
        )
        self._socket = socket
        self._backoff.reset()
        self._last_schema_mismatch = None
        logger.info("connected to matter controller %s (schema %d)", self._url, version)

        nodes = await self._bootstrap_listen(socket)
        self._nodes = {n.node_id: n for n in (_node_from(d) for d in (nodes or []))}
        for listener in self._listeners:
            await listener.on_connected(list(self._nodes.values()))
        while True:
            await self._dispatch(json.loads(await socket.recv()))

    async def _bootstrap_listen(self, socket: MatterSocket) -> Any:
        """Send start_listening and pump messages until its own reply
        resolves. Unlike `_request`, this polls the raw future directly
        instead of wrapping the call in a Task: a Task's own `done()` only
        flips true after an extra event-loop round trip once its awaited
        future resolves, which would need one more `recv()` that the server
        never sends, deadlocking the pump loop."""
        message_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await socket.send(
            json.dumps({"message_id": message_id, "command": "start_listening", "args": {}})
        )
        async with asyncio.timeout(self._bootstrap_timeout):
            while not future.done():
                await self._dispatch(json.loads(await socket.recv()))
            return future.result()

    async def _teardown(self) -> None:
        was_connected = self._socket is not None
        self._socket = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ControllerUnavailable("connection lost"))
        self._pending.clear()
        if was_connected:
            for listener in self._listeners:
                await listener.on_disconnected()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        # Invariant: a listener callback must never call back into this
        # controller (send/read/commission_with_code/...). The reply to any
        # such call could only be delivered by this same loop, on this same
        # task, so a callback that awaited one would deadlock.
        if "message_id" in message:
            future = self._pending.pop(str(message["message_id"]), None)
            if future is None or future.done():
                return
            if "error_code" in message:
                future.set_exception(
                    MatterError(int(message["error_code"]), str(message.get("details", "")))
                )
            else:
                future.set_result(message.get("result"))
            return
        event = message.get("event")
        data = message.get("data")
        if event in ("node_added", "node_updated") and isinstance(data, dict):
            node = _node_from(data)
            self._nodes[node.node_id] = node
            for listener in self._listeners:
                await listener.on_node(node)
        elif event == "node_removed":
            # Accept either a bare node ID or {"node_id": n}; the observed
            # server shape is unconfirmed for this event.
            node_id = int(data["node_id"]) if isinstance(data, dict) else int(data)  # type: ignore[arg-type]
            self._nodes.pop(node_id, None)
            for listener in self._listeners:
                await listener.on_node_removed(node_id)
        elif event == "attribute_updated" and isinstance(data, list) and len(data) == 3:
            node_id, path, value = int(data[0]), str(data[1]), data[2]
            existing = self._nodes.get(node_id)
            if existing is not None:
                attributes = dict(existing.attributes)
                attributes[path] = value
                self._nodes[node_id] = existing.model_copy(update={"attributes": attributes})
            for listener in self._listeners:
                await listener.on_attribute(node_id, path, value)

    # --- requests -------------------------------------------------------------

    def _require_connected(self) -> None:
        if self._socket is None:
            raise ControllerUnavailable("not connected to the matter controller")

    async def _request(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - overrides self._request_timeout, not a bare wait
    ) -> Any:
        self._require_connected()
        assert self._socket is not None
        message_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        # Serialised as-is via json.dumps: node IDs are plain Python ints, and
        # matter.js models NodeId as a BigInt. If the server ever wants node
        # IDs sent as strings instead, this is the single point to adapt.
        await self._socket.send(
            json.dumps({"message_id": message_id, "command": command, "args": args})
        )
        effective_timeout = self._request_timeout if timeout is None else timeout
        try:
            async with asyncio.timeout(effective_timeout):
                return await future
        except TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise ControllerUnavailable(f"{command}: no reply in {effective_timeout:.0f}s") from exc
