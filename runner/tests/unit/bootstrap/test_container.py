"""Spec §13: exposing lights to tier 2/3 jobs is out of scope. Voice keeps
its own gateway (voice_factory.py); job gateways must never actuate a bulb,
even when a job's own allowlist names a lights.* tool."""

from __future__ import annotations

from atlas.bootstrap.connectors_factory import build_connectors, gateway_for
from atlas.config import Settings
from atlas.connectors.domain.tools import ToolCall
from tests.factories import propose_tier1_definition


async def test_job_gateway_never_actuates_lights() -> None:
    settings = Settings(_env_file=None)
    connectors = build_connectors(settings)
    # The container's own gateway_for(...) call for jobs passes no `lights`
    # argument, so it takes ToolGateway's lights=None default -- build it
    # the same way here, with an allowlist that names lights.state anyway.
    definition = propose_tier1_definition(
        tools=["home-assistant.get_state", "home-assistant.turn_off", "lights.state"]
    )
    gateway = gateway_for(definition, connectors)

    result = await gateway.call(ToolCall(tool="lights.state"))

    assert result.is_error is True
