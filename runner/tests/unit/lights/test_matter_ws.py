"""MatterWsClient against an in-memory socket: handshake, schema refusal,
correlation, events, error mapping, reconnect. No network anywhere."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import JsonValue

from atlas.lights.application.ports import ControllerUnavailable, MatterError, MatterNode
from atlas.lights.infrastructure.matter_ws import MatterWsClient, SchemaMismatch
from atlas.shared.backoff import ReconnectBackoff

SERVER_INFO = {
    "fabric_id": 1,
    "compressed_fabric_id": 2,
    "schema_version": 13,
    "min_supported_schema_version": 11,
    "sdk_version": "matter-server/1.4.0",
    "wifi_credentials_set": False,
    "thread_credentials_set": False,
    "bluetooth_enabled": False,
}

NODE: dict[str, JsonValue] = {
    "node_id": 1,
    "date_commissioned": "2026-09-12T00:00:00",
    "last_interview": "2026-09-12T00:00:00",
    "interview_version": 6,
    "available": True,
    "is_bridge": False,
    "attributes": {"1/6/0": False, "0/40/3": "E12 RGBCW"},
    "attribute_subscriptions": [],
}


class FakeSocket:
    """Scripted server. `inbound` is what the client will receive, in order;
    the fake answers start_listening and other commands from `replies`."""

    def __init__(self, server_info: dict[str, Any] = SERVER_INFO) -> None:
        self.inbound: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.replies: dict[str, Any] = {"start_listening": [NODE]}
        self.errors: dict[str, tuple[int, str]] = {}
        # Commands recorded in `sent` but never answered: a reply that never
        # arrives, simulating a request left pending at disconnect.
        self.silence: set[str] = set()
        # Commands whose reply is delayed by this many seconds before being
        # queued, simulating a slow controller (e.g. real commissioning).
        self.delays: dict[str, float] = {}
        self.inbound.put_nowait(json.dumps(server_info))
        self.closed = asyncio.Event()

    async def send(self, message: str) -> None:
        request = json.loads(message)
        self.sent.append(request)
        command = request["command"]
        if command in self.silence:
            return
        if command in self.errors:
            code, details = self.errors[command]
            reply = {"message_id": request["message_id"], "error_code": code, "details": details}
        else:
            reply = {"message_id": request["message_id"], "result": self.replies.get(command)}
        delay = self.delays.get(command)
        if delay:
            await asyncio.sleep(delay)
        await self.inbound.put(json.dumps(reply))

    async def recv(self) -> str:
        message = await self.inbound.get()
        if message == "<close>":
            raise ConnectionError("socket closed")
        return message

    async def push_event(self, event: str, data: JsonValue) -> None:
        await self.inbound.put(json.dumps({"event": event, "data": data}))

    def close(self) -> None:
        self.inbound.put_nowait("<close>")


class Recorder:
    def __init__(self) -> None:
        self.connected: list[list[MatterNode]] = []
        self.disconnected = 0
        self.attributes: list[tuple[int, str, JsonValue]] = []
        self.nodes: list[MatterNode] = []
        self.removed: list[int] = []

    async def on_connected(self, nodes: list[MatterNode]) -> None:
        self.connected.append(nodes)

    async def on_disconnected(self) -> None:
        self.disconnected += 1

    async def on_node(self, node: MatterNode) -> None:
        self.nodes.append(node)

    async def on_node_removed(self, node_id: int) -> None:
        self.removed.append(node_id)

    async def on_attribute(self, node_id: int, path: str, value: JsonValue) -> None:
        self.attributes.append((node_id, path, value))


def _client(sockets: list[FakeSocket], sleeps: list[float], **kwargs: Any) -> MatterWsClient:
    remaining = list(sockets)

    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[FakeSocket]:
        if not remaining:
            raise ConnectionError("no more sockets")
        yield remaining.pop(0)

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    return MatterWsClient(
        "ws://test/ws",
        connect=connect,
        sleep=sleep,
        backoff=ReconnectBackoff(initial=1.0, jitter=lambda: 1.0),
        **kwargs,
    )


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_handshake_start_listening_and_connected_callback() -> None:
    socket = FakeSocket()
    recorder = Recorder()
    client = _client([socket], [])
    client.subscribe(recorder)
    task = asyncio.create_task(client.run())
    await _settle()
    assert client.connected
    assert socket.sent[0]["command"] == "start_listening"
    assert [n.node_id for n in recorder.connected[0]] == [1]
    assert (await client.nodes())[0].product_name == "E12 RGBCW"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _bad_schema_socket() -> FakeSocket:
    return FakeSocket({**SERVER_INFO, "schema_version": 14, "min_supported_schema_version": 14})


async def test_schema_outside_the_window_is_refused_and_backs_off() -> None:
    # A fresh socket per attempt: each represents a distinct TCP connection
    # to the same still-unupgraded server, each with its own server_info.
    sockets = [_bad_schema_socket(), _bad_schema_socket(), _bad_schema_socket()]
    sleeps: list[float] = []
    client = _client(sockets, sleeps)
    with pytest.raises(asyncio.CancelledError):
        await client.run()
    assert not client.connected
    assert all(s.sent == [] for s in sockets), "never sends start_listening to an unknown schema"
    assert sleeps == [1.0, 2.0, 4.0]


async def test_schema_mismatch_recovers_after_an_upgrade() -> None:
    """A schema mismatch is refused, not fatal: once the controller is
    upgraded (or a proxy in front of it starts reporting a good schema),
    the client picks it up on the very next scheduled reconnect."""
    sockets = [_bad_schema_socket(), _bad_schema_socket(), FakeSocket()]
    sleeps: list[float] = []
    recorder = Recorder()
    client = _client(sockets, sleeps)
    client.subscribe(recorder)
    task = asyncio.create_task(client.run())
    await _settle()
    assert sleeps == [1.0, 2.0]
    assert client.connected
    assert len(recorder.connected) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_schema_mismatch_is_a_named_error() -> None:
    assert issubclass(SchemaMismatch, Exception)


async def test_send_correlates_by_message_id_and_maps_errors() -> None:
    socket = FakeSocket()
    socket.errors["device_command"] = (3, "node not ready")
    client = _client([socket], [])
    task = asyncio.create_task(client.run())
    await _settle()
    with pytest.raises(MatterError) as excinfo:
        await client.send(1, 1, 6, "on", {})
    assert excinfo.value.code == 3
    request = socket.sent[-1]
    assert request["command"] == "device_command"
    assert request["args"] == {
        "node_id": 1,
        "endpoint_id": 1,
        "cluster_id": 6,
        "command_name": "on",
        "payload": {},
    }
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_events_are_dispatched() -> None:
    socket = FakeSocket()
    recorder = Recorder()
    client = _client([socket], [])
    client.subscribe(recorder)
    task = asyncio.create_task(client.run())
    await _settle()
    await socket.push_event("attribute_updated", [1, "1/6/0", True])
    await socket.push_event("node_updated", {**NODE, "available": False})
    await socket.push_event("node_removed", 1)
    await _settle()
    assert recorder.attributes == [(1, "1/6/0", True)]
    assert recorder.nodes[0].available is False
    assert recorder.removed == [1]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_node_removed_accepts_a_bare_int_or_a_dict() -> None:
    socket = FakeSocket()
    recorder = Recorder()
    client = _client([socket], [])
    client.subscribe(recorder)
    task = asyncio.create_task(client.run())
    await _settle()
    await socket.push_event("node_removed", 1)
    await socket.push_event("node_removed", {"node_id": 2})
    await _settle()
    assert recorder.removed == [1, 2]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_disconnect_fails_pending_and_new_requests_then_reconnects() -> None:
    first, second = FakeSocket(), FakeSocket()
    recorder = Recorder()
    sleeps: list[float] = []
    client = _client([first, second], sleeps)
    client.subscribe(recorder)
    task = asyncio.create_task(client.run())
    await _settle()
    first.close()
    await _settle()
    assert recorder.disconnected == 1
    # Backoff sleep happened, second socket connected, listeners re-seeded.
    assert sleeps == [1.0]
    assert len(recorder.connected) == 2
    assert client.connected
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_requests_while_disconnected_fail_fast() -> None:
    client = _client([], [])
    with pytest.raises(ControllerUnavailable):
        await client.send(1, 1, 6, "on", {})
    with pytest.raises(ControllerUnavailable):
        await client.nodes()


async def test_server_info_is_unavailable_while_disconnected() -> None:
    # Only one socket: once it closes, reconnect attempts exhaust the
    # fixture and the run() task ends on its own via the sleep-cap, all
    # synchronously — no hang, and a deterministic disconnected window to
    # assert against.
    socket = FakeSocket()
    client = _client([socket], [])
    task = asyncio.create_task(client.run())
    await _settle()
    info = await client.server_info()
    assert info.schema_version == 13
    socket.close()
    await _settle()
    with pytest.raises(ControllerUnavailable):
        await client.server_info()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_pending_request_fails_when_the_socket_disconnects() -> None:
    first, second = FakeSocket(), FakeSocket()
    first.silence.add("device_command")
    client = _client([first, second], [])
    task = asyncio.create_task(client.run())
    await _settle()

    async def _send() -> None:
        await client.send(1, 1, 6, "on", {})

    pending = asyncio.create_task(_send())
    await _settle()
    assert first.sent[-1]["command"] == "device_command"
    first.close()
    await _settle()
    with pytest.raises(ControllerUnavailable):
        await pending
    assert client.connected, "reconnects into the second socket"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_commission_outlives_the_default_request_timeout() -> None:
    """Real commissioning (network join plus interview) takes 30-120s, far
    longer than the default per-request timeout used for ordinary reads and
    writes. commission_with_code must wait on its own, longer,
    commission_timeout rather than the request_timeout."""
    socket = FakeSocket()
    socket.replies["commission_with_code"] = {**NODE, "node_id": 9}
    socket.delays["commission_with_code"] = 0.05
    client = _client([socket], [], request_timeout=0.01, commission_timeout=1.0)
    task = asyncio.create_task(client.run())
    await _settle()
    assert await client.commission_with_code("12345678901", network_only=True) == 9
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_read_logs_a_warning_on_an_unexpected_reply_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    socket = FakeSocket()
    socket.replies["read_attribute"] = [1, 2, 3]
    client = _client([socket], [])
    task = asyncio.create_task(client.run())
    await _settle()
    with caplog.at_level("WARNING"):
        result = await client.read(1, "1/*/*")
    assert result == {}
    assert any("read_attribute" in record.getMessage() for record in caplog.records)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_commission_returns_the_node_id_and_read_returns_attributes() -> None:
    socket = FakeSocket()
    socket.replies["commission_with_code"] = {**NODE, "node_id": 9}
    socket.replies["read_attribute"] = {"1/6/0": True, "1/8/0": 200}
    client = _client([socket], [])
    task = asyncio.create_task(client.run())
    await _settle()
    assert await client.commission_with_code("12345678901", network_only=True) == 9
    assert socket.sent[-1]["args"] == {"code": "12345678901", "network_only": True}
    assert await client.read(1, "1/*/*") == {"1/6/0": True, "1/8/0": 200}
    assert socket.sent[-1]["args"] == {"node_id": 1, "attribute_path": "1/*/*"}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
