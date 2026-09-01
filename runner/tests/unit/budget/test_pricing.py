"""Pricing: exact integer costs, rounding up, override, safe fallback."""

from __future__ import annotations

from atlas.budget.domain.ledger import usd
from atlas.budget.infrastructure.pricing import StaticPricingTable
from tests.factories import usage


def test_sonnet_costs_match_the_table() -> None:
    table = StaticPricingTable()
    # 1M in at $3 + 1M out at $15 = $18.
    cost = table.cost("claude-sonnet-5", usage(1_000_000, 1_000_000))
    assert cost == usd("18.00")


def test_costs_round_up_never_down() -> None:
    table = StaticPricingTable()
    # 1 input token at $3/Mtok is 3 millionths of a dollar → 3 micros exactly;
    # 1 output token at $15/Mtok → 15 micros. Fractions must ceil.
    assert table.cost("claude-sonnet-5", usage(1, 1)) == 3 + 15
    # $0.80/Mtok input: 1 token = 0.8 micros → must become 1, not 0.
    assert table.cost("claude-haiku-4-5", usage(1, 0)) == 1


def test_unknown_model_uses_most_expensive_fallback() -> None:
    table = StaticPricingTable()
    unknown = table.cost("some-future-model", usage(1_000_000, 0))
    assert unknown == usd("15.00")


def test_override_wins_over_the_table() -> None:
    table = StaticPricingTable(input_per_mtok=usd("1.00"), output_per_mtok=usd("2.00"))
    assert table.cost("claude-sonnet-5", usage(1_000_000, 1_000_000)) == usd("3.00")


def test_zero_usage_costs_zero() -> None:
    assert StaticPricingTable().cost("claude-sonnet-5", usage(0, 0)) == 0
