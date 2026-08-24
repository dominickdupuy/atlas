"""The §7 schema validation matrix: one test per rule, each fixture violating
exactly one thing. These rules ARE the D7/D8 guardrails."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pihome.jobs.domain.definition import JobDefinition, Tier
from tests.factories import tier1_spec, tier2_spec, tier3_spec


def test_valid_tier1_parses() -> None:
    definition = JobDefinition.model_validate(tier1_spec())
    assert definition.tier is Tier.DETERMINISTIC
    assert definition.enabled is True


def test_tier_defaults_to_1() -> None:
    spec = tier1_spec()
    del spec["tier"]
    assert JobDefinition.model_validate(spec).tier is Tier.DETERMINISTIC


def test_valid_tier2_parses() -> None:
    assert JobDefinition.model_validate(tier2_spec()).tier is Tier.SUMMARIZE


def test_valid_tier3_parses() -> None:
    assert JobDefinition.model_validate(tier3_spec()).tier is Tier.AGENT


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        # D8: write requires explicit consent.
        ({"mode": "write", "propose": None}, "auto_approve"),
        # auto_approve outside write mode is meaningless and likely a typo.
        ({"auto_approve": True}, "only meaningful"),
        # D16: propose mode needs a TTL.
        (
            {
                "mode": "propose",
                "propose": {"tool": "google-calendar.list_events", "summary": "x"},
            },
            "approval_ttl_seconds",
        ),
        # D7: tier 1 has no model involvement.
        ({"synthesize": "Summarize {{events}}"}, "tier 1 forbids 'synthesize'"),
        ({"goal": "do things"}, "tier 1 forbids 'goal'"),
        ({"steps": []}, "tier 1 requires at least one step"),
        # read cannot mutate, so an action template is a contradiction.
        (
            {"propose": {"tool": "google-calendar.list_events", "summary": "x"}},
            "mode 'read' forbids",
        ),
        # every referenced tool must be allowlisted.
        (
            {"steps": [{"tool": "github.list_notifications", "args": {}, "save_as": "x"}]},
            "not allowlisted",
        ),
        ({"schedule": "not cron"}, "cron"),
        ({"unknown_field": 1}, "unknown_field"),
    ],
)
def test_tier1_violations(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        JobDefinition.model_validate(tier1_spec(**overrides))


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"synthesize": None}, "tier 2 requires 'synthesize'"),
        ({"goal": "also a goal"}, "tier 2 forbids 'goal'"),
        ({"steps": []}, "tier 2 requires steps"),
        (
            {"budget": {"max_wall_clock_seconds": 60, "max_tool_calls": 10}},
            "tier 2 requires budget.max_tokens",
        ),
    ],
)
def test_tier2_violations(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        JobDefinition.model_validate(tier2_spec(**overrides))


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"goal": None}, "tier 3 requires 'goal'"),
        ({"synthesize": "text"}, "tier 3 forbids 'synthesize'"),
        (
            {
                "propose": {"tool": "google-calendar.list_events", "summary": "x"},
            },
            "tier 3 forbids a static 'propose'",
        ),
        # Guardrails before capability: no unattended tier-3 writes.
        ({"mode": "write", "auto_approve": True}, "tier 3 with mode 'write'"),
        (
            {"budget": {"max_wall_clock_seconds": 120, "max_tool_calls": 10}},
            "tier 3 requires budget.max_tokens",
        ),
    ],
)
def test_tier3_violations(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        JobDefinition.model_validate(tier3_spec(**overrides))


def test_propose_mode_requires_action_template_on_tier1() -> None:
    with pytest.raises(ValidationError, match="requires a 'propose' action template"):
        JobDefinition.model_validate(tier1_spec(mode="propose", approval_ttl_seconds=3600))


def test_write_mode_with_auto_approve_and_template_is_valid() -> None:
    definition = JobDefinition.model_validate(
        tier1_spec(
            mode="write",
            auto_approve=True,
            tools=["google-calendar.list_events", "home-assistant.turn_off"],
            propose={"tool": "home-assistant.turn_off", "summary": "lights out"},
        )
    )
    assert definition.auto_approve is True
