"""Reachability probes for the third-party services the board reports on.

Deliberately a TCP connect rather than a Docker API query. Three reasons:
the runner then needs no access to the Docker socket (which is root-equivalent
on this host); "listening on its port" is a strictly better health signal than
"container is running", since a wedged Home Assistant satisfies the second and
fails the first; and it keeps working if a service is ever moved out of a
container. The cost is that a stopped container and a crashed process look
identical here, which the board does not need to distinguish — both mean the
same thing to the person reading it across the room.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2.0

Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    endpoint: str
    reachable: bool
    latency_ms: float | None = None
    detail: str | None = None


class TcpServiceProbe:
    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Connector | None = None,
    ) -> None:
        self.name = name
        self._host = host
        self._port = port
        self._timeout = timeout
        self._connect: Connector = connect or asyncio.open_connection

    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port}"

    async def check(self) -> ServiceStatus:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(self._timeout):
                _, writer = await self._connect(self._host, self._port)
        except TimeoutError:
            return ServiceStatus(
                name=self.name,
                endpoint=self.endpoint,
                reachable=False,
                detail=f"no response in {self._timeout:.0f}s",
            )
        except OSError as exc:
            return ServiceStatus(
                name=self.name, endpoint=self.endpoint, reachable=False, detail=str(exc)
            )
        latency_ms = (loop.time() - started) * 1000.0
        writer.close()
        # The peer hanging up on an unspoken protocol is expected; we already
        # have the only answer we wanted, which is that it accepted at all.
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return ServiceStatus(
            name=self.name, endpoint=self.endpoint, reachable=True, latency_ms=latency_ms
        )


async def probe_all(probes: tuple[TcpServiceProbe, ...]) -> list[ServiceStatus]:
    """Probes run concurrently: the board polls every 10s and a down service
    costs a full timeout, so serialising them would blow the budget."""
    if not probes:
        return []
    results = await asyncio.gather(*(probe.check() for probe in probes), return_exceptions=True)
    statuses: list[ServiceStatus] = []
    for probe, result in zip(probes, results, strict=True):
        if isinstance(result, ServiceStatus):
            statuses.append(result)
            continue
        logger.warning("probe %s raised unexpectedly: %r", probe.name, result)
        statuses.append(
            ServiceStatus(
                name=probe.name,
                endpoint=probe.endpoint,
                reachable=False,
                detail=f"probe error: {result}",
            )
        )
    return statuses
