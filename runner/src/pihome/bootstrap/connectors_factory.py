"""Connector wiring shared by parent and child.

The parent needs connectors to execute approved frozen payloads (D16); the
child needs them to run tiers. Both get the identical set from here, chosen
by PIHOME_PROFILE: `dev` is fully stubbed (zero credentials), `prod` wires
the real adapters and refuses to half-configure them silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pihome.config import Settings
from pihome.connectors.application.gateway import ToolGateway
from pihome.connectors.application.ports import LlmProvider, McpClient, Notifier, WeatherPort
from pihome.connectors.application.tier_executors import (
    Tier1Executor,
    Tier2Executor,
    Tier3AgentExecutor,
)
from pihome.connectors.domain.tools import ToolAllowlist
from pihome.connectors.infrastructure.stubs import (
    LogNotifier,
    StubLlmProvider,
    StubMcpClient,
    StubWeather,
)
from pihome.jobs.domain.definition import JobDefinition, Tier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Connectors:
    clients: dict[str, McpClient]
    weather: WeatherPort
    llm: LlmProvider


def build_connectors(settings: Settings) -> Connectors:
    if settings.profile == "dev":
        stub = StubMcpClient()
        return Connectors(
            clients={"google-calendar": stub, "github": stub, "home-assistant": stub},
            weather=StubWeather(),
            llm=StubLlmProvider(),
        )

    from pihome.connectors.infrastructure.anthropic_llm import AnthropicLlmProvider
    from pihome.connectors.infrastructure.mcp_client import McpHttpClient
    from pihome.connectors.infrastructure.weather_http import OpenMeteoWeatherClient

    if not settings.anthropic_api_key:
        raise RuntimeError("PIHOME_PROFILE=prod requires ANTHROPIC_API_KEY")

    clients: dict[str, McpClient] = {}
    if settings.mcp_github_url:
        clients["github"] = McpHttpClient(settings.mcp_github_url)
    if settings.mcp_gcal_url:
        clients["google-calendar"] = McpHttpClient(settings.mcp_gcal_url)
    if settings.mcp_home_assistant_url:
        clients["home-assistant"] = McpHttpClient(
            settings.mcp_home_assistant_url,
            headers={"Authorization": f"Bearer {settings.mcp_home_assistant_token}"},
        )
    if not clients:
        logger.warning("prod profile with no MCP endpoints configured; only weather will work")

    return Connectors(
        clients=clients,
        weather=OpenMeteoWeatherClient(),
        llm=AnthropicLlmProvider(settings.anthropic_api_key, settings.model),
    )


def build_notifier(settings: Settings) -> Notifier:
    if settings.ntfy_token:
        from pihome.connectors.infrastructure.ntfy_notifier import NtfyNotifier

        return NtfyNotifier(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token)
    logger.info("no PIHOME_NTFY_TOKEN configured; notifications go to the log")
    return LogNotifier()


def gateway_for(definition: JobDefinition, connectors: Connectors) -> ToolGateway:
    """One gateway per run/action: fresh call counter, the job's own
    allowlist (spec §7)."""
    return ToolGateway(
        allowlist=ToolAllowlist(tools=frozenset(definition.tools)),
        clients=connectors.clients,
        weather=connectors.weather,
        max_tool_calls=definition.budget.max_tool_calls,
    )


def executor_for(
    definition: JobDefinition, connectors: Connectors
) -> Tier1Executor | Tier2Executor | Tier3AgentExecutor:
    gateway = gateway_for(definition, connectors)
    match definition.tier:
        case Tier.DETERMINISTIC:
            return Tier1Executor(gateway)
        case Tier.SUMMARIZE:
            return Tier2Executor(gateway, connectors.llm)
        case Tier.AGENT:
            return Tier3AgentExecutor(gateway, connectors.llm)
