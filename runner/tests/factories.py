"""Definition and report factories: valid baselines that individual tests
override to create exactly one violation or scenario."""

from __future__ import annotations

from typing import Any

from atlas.connectors.domain.tools import TokenUsage, ToolCall
from atlas.jobs.domain.definition import JobDefinition
from atlas.jobs.domain.run import ProposedAction, RunReport


def tier1_spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "id": "calendar-today",
        "description": "Pull today's calendar",
        "schedule": "*/30 * * * *",
        "tier": 1,
        "mode": "read",
        "tools": ["google-calendar.list_events"],
        "steps": [
            {"tool": "google-calendar.list_events", "args": {"range": "today"}, "save_as": "events"}
        ],
    }
    spec.update(overrides)
    return spec


def tier1_definition(**overrides: Any) -> JobDefinition:
    return JobDefinition.model_validate(tier1_spec(**overrides))


def tier2_spec(**overrides: Any) -> dict[str, Any]:
    spec = tier1_spec(
        id="morning-briefing",
        tier=2,
        synthesize="Summarize: {{events}}",
        budget={"max_tokens": 4000, "max_wall_clock_seconds": 60, "max_tool_calls": 10},
    )
    spec.update(overrides)
    return spec


def tier2_definition(**overrides: Any) -> JobDefinition:
    return JobDefinition.model_validate(tier2_spec(**overrides))


def tier3_spec(**overrides: Any) -> dict[str, Any]:
    spec = tier1_spec(
        id="conflict-finder",
        tier=3,
        mode="propose",
        approval_ttl_seconds=3600,
        goal="Find conflicts and propose fixes",
        budget={"max_tokens": 8000, "max_wall_clock_seconds": 120, "max_tool_calls": 10},
    )
    spec.update(overrides)
    return spec


def tier3_definition(**overrides: Any) -> JobDefinition:
    return JobDefinition.model_validate(tier3_spec(**overrides))


def propose_tier1_definition(**overrides: Any) -> JobDefinition:
    """Tier 1 / propose: lights-off with a static action template."""
    spec = tier1_spec(
        id="lights-out",
        mode="propose",
        approval_ttl_seconds=3600,
        tools=["home-assistant.get_state", "home-assistant.turn_off"],
        steps=[{"tool": "home-assistant.get_state", "args": {}, "save_as": "state"}],
        propose={
            "tool": "home-assistant.turn_off",
            "args": {"entity_id": "light.living_room"},
            "summary": "Turn off the living room lights",
        },
    )
    spec.update(overrides)
    return JobDefinition.model_validate(spec)


def ok_report(**overrides: Any) -> RunReport:
    values: dict[str, Any] = {"status": "ok", "output": {"events": []}}
    values.update(overrides)
    return RunReport.model_validate(values)


def proposal(tool: str = "home-assistant.turn_off", summary: str = "Turn off") -> ProposedAction:
    return ProposedAction(call=ToolCall(tool=tool, args={}), summary=summary)


def usage(input_tokens: int = 350, output_tokens: int = 120) -> TokenUsage:
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
