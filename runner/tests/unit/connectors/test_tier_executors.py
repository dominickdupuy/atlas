"""Tier executors against the stub connectors — dev behavior IS tested
behavior, by construction."""

from __future__ import annotations

import json

from pydantic import JsonValue

from pihome.connectors.application.gateway import ToolGateway
from pihome.connectors.application.tier_executors import (
    Tier1Executor,
    Tier2Executor,
    Tier3AgentExecutor,
    render_text,
    render_value,
)
from pihome.connectors.domain.tools import ToolAllowlist
from pihome.connectors.infrastructure.stubs import StubLlmProvider, StubMcpClient, StubWeather
from pihome.jobs.domain.definition import JobDefinition
from tests.factories import (
    propose_tier1_definition,
    tier1_definition,
    tier2_definition,
    tier3_definition,
)


def _gateway(definition: JobDefinition) -> ToolGateway:
    return ToolGateway(
        allowlist=ToolAllowlist(tools=frozenset(definition.tools)),
        clients={
            "google-calendar": StubMcpClient(),
            "home-assistant": StubMcpClient(),
            "github": StubMcpClient(),
        },
        weather=StubWeather(),
        max_tool_calls=definition.budget.max_tool_calls,
    )


# --- templating ---------------------------------------------------------------


def test_render_text_substitutes_and_serializes() -> None:
    bindings: dict[str, JsonValue] = {"events": [{"title": "Standup"}], "name": "Dominick"}
    assert render_text("Hi {{name}}", bindings) == "Hi Dominick"
    assert render_text("Data: {{events}}", bindings) == 'Data: [{"title": "Standup"}]'


def test_render_value_exact_placeholder_keeps_type() -> None:
    bindings: dict[str, JsonValue] = {"events": [1, 2, 3]}
    assert render_value("{{events}}", bindings) == [1, 2, 3]
    assert render_value({"nested": "{{events}}"}, bindings) == {"nested": [1, 2, 3]}


def test_render_unknown_binding_raises() -> None:
    try:
        render_text("{{missing}}", {})
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected KeyError")


# --- tier 1 -------------------------------------------------------------------


async def test_tier1_runs_steps_and_binds_results() -> None:
    definition = tier1_definition()
    report = await Tier1Executor(_gateway(definition)).execute(definition)
    assert report.status == "ok"
    assert isinstance(report.output, dict)
    assert "events" in report.output
    assert report.tool_calls == 1
    assert report.proposals == ()


async def test_tier1_propose_renders_the_action_template() -> None:
    definition = propose_tier1_definition()
    report = await Tier1Executor(_gateway(definition)).execute(definition)
    assert report.status == "ok"
    assert len(report.proposals) == 1
    action = report.proposals[0]
    assert action.call.tool == "home-assistant.turn_off"
    assert action.summary == "Turn off the living room lights"


async def test_tier1_step_failure_stops_the_job() -> None:
    definition = tier1_definition(
        tools=["google-calendar.list_events", "google-calendar.nonexistent"],
        steps=[
            {"tool": "google-calendar.nonexistent", "args": {}, "save_as": "a"},
            {"tool": "google-calendar.list_events", "args": {}, "save_as": "b"},
        ],
    )
    report = await Tier1Executor(_gateway(definition)).execute(definition)
    assert report.status == "error"
    assert report.error is not None and "'a'" in report.error
    assert report.tool_calls == 1  # second step never ran


# --- tier 2 -------------------------------------------------------------------


async def test_tier2_makes_exactly_one_model_call_with_rendered_prompt() -> None:
    definition = tier2_definition()
    llm = StubLlmProvider(responses=["The briefing."])
    report = await Tier2Executor(_gateway(definition), llm).execute(definition)
    assert report.status == "ok"
    assert report.output == "The briefing."
    assert len(llm.requests) == 1
    assert "Standup" in llm.requests[0].prompt  # step result made it in
    assert llm.requests[0].max_tokens == 4000
    assert report.usage.total == 470  # scripted stub usage: budget-visible


# --- tier 3 (stub) ------------------------------------------------------------


async def test_tier3_parses_allowlisted_proposals() -> None:
    definition = tier3_definition(
        tools=["google-calendar.list_events", "google-calendar.update_event"]
    )
    scripted = json.dumps(
        [
            {
                "tool": "google-calendar.update_event",
                "args": {"event_id": "abc", "start": "10:00"},
                "summary": "Move standup to 10:00",
            },
            {"tool": "not-allowed.delete_everything", "args": {}, "summary": "nope"},
            "garbage",
        ]
    )
    llm = StubLlmProvider(responses=[scripted])
    report = await Tier3AgentExecutor(_gateway(definition), llm).execute(definition)
    assert report.status == "ok"
    assert len(report.proposals) == 1  # non-allowlisted and malformed were dropped
    assert report.proposals[0].call.tool == "google-calendar.update_event"


async def test_tier3_non_json_output_proposes_nothing() -> None:
    definition = tier3_definition()
    llm = StubLlmProvider(responses=["I think you should consider..."])
    report = await Tier3AgentExecutor(_gateway(definition), llm).execute(definition)
    assert report.status == "ok"
    assert report.proposals == ()
