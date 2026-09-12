"""Operator actions for the CLI (spec 6). Each opens its own short-lived
controller connection; the running service is not involved."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from atlas.lights.application.ports import ControllerUnavailable, MatterController
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.domain.matter import IDENTIFY_CLUSTER
from atlas.lights.infrastructure.matter_ws import MatterWsClient


class NodeSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: int
    vendor: str | None
    product: str | None
    label: str | None
    available: bool
    name: str | None


async def list_nodes(controller: MatterController, registry: LightsRegistry) -> list[NodeSummary]:
    summaries: list[NodeSummary] = []
    for node in sorted(await controller.nodes(), key=lambda n: n.node_id):
        light = registry.by_node(node.node_id)
        summaries.append(
            NodeSummary(
                node_id=node.node_id,
                vendor=node.vendor_name,
                product=node.product_name,
                label=node.node_label,
                available=node.available,
                name=light.name if light else None,
            )
        )
    return summaries


async def commission(controller: MatterController, code: str) -> int:
    """network_only: the bulbs are already on the Wi-Fi via Apple Home."""
    cleaned = "".join(ch for ch in code if ch.isalnum() or ch in ":.")
    return await controller.commission_with_code(cleaned, network_only=True)


async def identify(
    controller: MatterController, node_id: int, endpoint_id: int = 1, seconds: int = 10
) -> None:
    await controller.send(
        node_id, endpoint_id, IDENTIFY_CLUSTER, "identify", {"identifyTime": seconds}
    )


async def remove(controller: MatterController, node_id: int) -> None:
    await controller.remove_node(node_id)


async def with_controller[T](
    url: str,
    action: Callable[[MatterController], Awaitable[T]],
    *,
    timeout: float = 15.0,  # noqa: ASYNC109 - a CLI wait budget, not a per-request timeout
) -> T:
    client = MatterWsClient(url)
    task = asyncio.create_task(client.run(), name="matter-cli")
    try:
        try:
            async with asyncio.timeout(timeout):
                # ASYNC110: polling a plain bool on another object, not a flag
                # of our own we could instead flip via asyncio.Event.
                while not client.connected:  # noqa: ASYNC110
                    await asyncio.sleep(0.1)
        except TimeoutError as exc:
            raise ControllerUnavailable(f"no controller at {url} within {timeout:.0f}s") from exc
        return await action(client)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
