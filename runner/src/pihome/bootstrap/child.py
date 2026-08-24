"""Child composition (execute-job): connectors and a tier executor, nothing
else. No DB, no MQTT, no HTTP server — the child reports, the parent
decides (D14)."""

from __future__ import annotations

from pihome.bootstrap.connectors_factory import build_connectors, executor_for
from pihome.config import Settings
from pihome.connectors.application.tier_executors import (
    Tier1Executor,
    Tier2Executor,
    Tier3AgentExecutor,
)
from pihome.jobs.domain.definition import JobDefinition


def build_tier_executor(
    definition: JobDefinition,
) -> Tier1Executor | Tier2Executor | Tier3AgentExecutor:
    settings = Settings()
    connectors = build_connectors(settings)
    return executor_for(definition, connectors)
