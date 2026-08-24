"""Budget events → pihome/budget/status (D6)."""

from __future__ import annotations

from pihome.budget.domain.policy import BudgetStatus
from pihome.shared.events import DomainEvent


class BudgetStatusChanged(DomainEvent):
    status: BudgetStatus


class DailyCeilingReached(DomainEvent):
    """The moment the ceiling is crossed: the scheduler pauses (spec §8) and
    this is surfaced loudly on the board."""

    status: BudgetStatus
