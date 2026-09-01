"""Child composition (execute-job): connectors and a tier executor, nothing
else. No DB, no MQTT, no HTTP server — the child reports, the parent
decides (D14)."""

from __future__ import annotations

from atlas.bootstrap.connectors_factory import build_connectors, executor_for
from atlas.config import Settings
from atlas.connectors.application.tier_executors import (
    Tier1Executor,
    Tier2Executor,
    Tier3AgentExecutor,
)
from atlas.jobs.domain.definition import JobDefinition


def build_tier_executor(
    definition: JobDefinition,
) -> Tier1Executor | Tier2Executor | Tier3AgentExecutor:
    settings = Settings()
    connectors = build_connectors(settings)
    return executor_for(definition, connectors)
