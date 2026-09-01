"""Spend policy and integer money."""

from __future__ import annotations

import pytest

from atlas.budget.domain.ledger import UsdMicros, format_usd, usd
from atlas.budget.domain.policy import BudgetLevel, evaluate


@pytest.mark.parametrize(
    ("text", "micros"),
    [
        ("5.00", 5_000_000),
        ("0.50", 500_000),
        ("5", 5_000_000),
        ("0.000001", 1),
        ("12.345678", 12_345_678),
        ("12.3456789", 12_345_678),  # truncated past micro precision
    ],
)
def test_usd_parses_exactly(text: str, micros: int) -> None:
    assert usd(text) == micros


def test_format_usd() -> None:
    assert format_usd(usd("5.00")) == "$5.00"
    assert format_usd(UsdMicros(1_234_567)) == "$1.23"


@pytest.mark.parametrize(
    ("spent", "expected"),
    [
        ("0.00", BudgetLevel.OK),
        ("3.99", BudgetLevel.OK),
        ("4.00", BudgetLevel.WARNING),  # 80% boundary
        ("4.99", BudgetLevel.WARNING),
        ("5.00", BudgetLevel.EXHAUSTED),
        ("6.00", BudgetLevel.EXHAUSTED),
    ],
)
def test_evaluate_levels(spent: str, expected: BudgetLevel) -> None:
    status = evaluate(usd(spent), usd("5.00"))
    assert status.level is expected


def test_remaining_never_negative() -> None:
    status = evaluate(usd("6.00"), usd("5.00"))
    assert status.remaining_usd_micros == 0
