"""Payload executors: frozen payloads and auto-approved writes both go
through the connectors gateway under the owning job's own allowlist — the
identical path by construction, which is the point.

ToolGatewayPayloadExecutor implements the approvals FrozenPayloadExecutor
port (looked up by job id at decision time); DirectWriteExecutor implements
the jobs WriteExecutor port (definition already in hand).
"""

from __future__ import annotations

from collections.abc import Callable

from pihome.connectors.application.gateway import ToolGateway
from pihome.connectors.domain.tools import ToolResult
from pihome.jobs.domain.definition import JobDefinition
from pihome.jobs.domain.run import ProposedAction
from pihome.shared.ids import JobId

GatewayFactory = Callable[[JobDefinition], ToolGateway]
DefinitionLookup = Callable[[JobId], JobDefinition | None]


class ToolGatewayPayloadExecutor:
    def __init__(self, lookup: DefinitionLookup, gateway_factory: GatewayFactory) -> None:
        self._lookup = lookup
        self._gateway_factory = gateway_factory

    async def execute(self, job_id: JobId, action: ProposedAction) -> ToolResult:
        definition = self._lookup(job_id)
        if definition is None:
            return ToolResult(
                tool=action.call.tool,
                content=f"job {job_id!r} no longer exists; refusing to execute",
                is_error=True,
            )
        gateway = self._gateway_factory(definition)
        return await gateway.call(action.call)


class DirectWriteExecutor:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self._gateway_factory = gateway_factory

    async def execute(self, definition: JobDefinition, action: ProposedAction) -> ToolResult:
        gateway = self._gateway_factory(definition)
        return await gateway.call(action.call)
