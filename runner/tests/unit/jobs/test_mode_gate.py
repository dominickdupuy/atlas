"""The D8 decision table, exhaustively."""

from __future__ import annotations

from pihome.jobs.domain import policies
from tests.factories import (
    ok_report,
    proposal,
    propose_tier1_definition,
    tier1_definition,
    tier1_spec,
)


def test_read_publishes_and_drops_proposals() -> None:
    definition = tier1_definition()
    report = ok_report(proposals=[proposal()])
    outcome = policies.route(definition, report)
    assert isinstance(outcome, policies.PublishResult)
    assert len(outcome.dropped_proposals) == 1


def test_propose_with_proposals_routes_to_approval() -> None:
    definition = propose_tier1_definition()
    report = ok_report(proposals=[proposal()])
    outcome = policies.route(definition, report)
    assert isinstance(outcome, policies.ProposeApproval)
    assert outcome.proposals == report.proposals


def test_propose_with_nothing_to_propose_just_publishes() -> None:
    definition = propose_tier1_definition()
    outcome = policies.route(definition, ok_report())
    assert isinstance(outcome, policies.PublishResult)
    assert outcome.dropped_proposals == ()


def test_write_routes_to_execution() -> None:
    from pihome.jobs.domain.definition import JobDefinition

    definition = JobDefinition.model_validate(
        tier1_spec(
            mode="write",
            auto_approve=True,
            tools=["google-calendar.list_events", "home-assistant.turn_off"],
            propose={"tool": "home-assistant.turn_off", "summary": "lights out"},
        )
    )
    report = ok_report(proposals=[proposal()])
    outcome = policies.route(definition, report)
    assert isinstance(outcome, policies.ExecuteWrite)
    assert outcome.actions == report.proposals


def test_retry_policy_is_one_based() -> None:
    definition = tier1_definition(on_failure={"retry": 1})
    assert policies.should_retry(definition, attempt=1) is True
    assert policies.should_retry(definition, attempt=2) is False
    no_retry = tier1_definition()
    assert policies.should_retry(no_retry, attempt=1) is False
