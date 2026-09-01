"""Service reachability, with the network injected.

pytest runs with sockets disabled, which is the point: these tests describe
what the board shows when Home Assistant is down, and that must not depend
on whether anything is actually listening on the machine running the suite.
"""

from __future__ import annotations

import asyncio
from typing import cast

from atlas.telemetry.infrastructure.service_probes import (
    ServiceStatus,
    TcpServiceProbe,
    probe_all,
)


class _FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _RudeWriter(_FakeWriter):
    """A broker that drops the connection rather than closing politely."""

    async def wait_closed(self) -> None:
        raise ConnectionResetError("peer hung up")


def _accepting(writer: _FakeWriter | None = None):  # type: ignore[no-untyped-def]
    async def connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return (
            cast(asyncio.StreamReader, object()),
            cast(asyncio.StreamWriter, writer or _FakeWriter()),
        )

    return connect


async def _refusing(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    raise ConnectionRefusedError(f"[Errno 111] Connect call failed ('{host}', {port})")


async def _hanging(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def _exploding(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    raise RuntimeError("resolver blew up")


async def test_a_listening_service_is_reachable() -> None:
    writer = _FakeWriter()
    probe = TcpServiceProbe("mosquitto", "127.0.0.1", 1883, connect=_accepting(writer))

    status = await probe.check()

    assert status.reachable is True
    assert status.endpoint == "127.0.0.1:1883"
    assert status.latency_ms is not None
    assert writer.closed, "the probe must not leak the connection it opened"


async def test_a_refused_connection_reports_the_reason() -> None:
    probe = TcpServiceProbe("homeassistant", "127.0.0.1", 8123, connect=_refusing)

    status = await probe.check()

    assert status.reachable is False
    assert status.detail is not None and "Connect call failed" in status.detail


async def test_a_hanging_service_times_out_rather_than_stalling_the_board() -> None:
    probe = TcpServiceProbe("wedged", "127.0.0.1", 9999, timeout=0.01, connect=_hanging)

    status = await probe.check()

    assert status.reachable is False
    assert status.detail is not None and "no response" in status.detail


async def test_an_impolite_close_still_counts_as_reachable() -> None:
    """It accepted the connection; that is the entire question being asked."""
    probe = TcpServiceProbe("mosquitto", "127.0.0.1", 1883, connect=_accepting(_RudeWriter()))

    assert (await probe.check()).reachable is True


async def test_probe_all_runs_concurrently_and_keeps_order() -> None:
    probes = (
        TcpServiceProbe("homeassistant", "127.0.0.1", 8123, timeout=0.05, connect=_hanging),
        TcpServiceProbe("mosquitto", "127.0.0.1", 1883, connect=_accepting()),
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    statuses = await probe_all(probes)
    elapsed = loop.time() - started

    assert [status.name for status in statuses] == ["homeassistant", "mosquitto"]
    assert statuses[0].reachable is False
    assert statuses[1].reachable is True
    assert elapsed < 0.5, "a down service must not serialise behind the others"


async def test_an_unexpected_exception_becomes_a_down_status() -> None:
    """The board must render even if a probe misbehaves in a way we did not
    anticipate; one bad probe cannot take the whole status endpoint down."""
    probes = (TcpServiceProbe("weird", "127.0.0.1", 1, connect=_exploding),)

    statuses = await probe_all(probes)

    assert statuses[0].reachable is False
    assert statuses[0].detail is not None and "resolver blew up" in statuses[0].detail


async def test_no_probes_is_an_empty_list() -> None:
    assert await probe_all(()) == []


def test_status_is_immutable() -> None:
    status = ServiceStatus(name="x", endpoint="127.0.0.1:1", reachable=True)
    try:
        status.reachable = False  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ServiceStatus must be frozen")
