"""The spend policy: pure evaluation of today's ledger against the ceiling."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from atlas.budget.domain.ledger import UsdMicros

WARNING_FRACTION = 0.8


class BudgetLevel(StrEnum):
    OK = "ok"
    WARNING = "warning"
    EXHAUSTED = "exhausted"


class BudgetStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: BudgetLevel
    spent_usd_micros: int
    ceiling_usd_micros: int

    @property
    def remaining_usd_micros(self) -> int:
        return max(0, self.ceiling_usd_micros - self.spent_usd_micros)


def evaluate(spent: UsdMicros, ceiling: UsdMicros) -> BudgetStatus:
    if spent >= ceiling:
        level = BudgetLevel.EXHAUSTED
    elif spent >= int(ceiling * WARNING_FRACTION):
        level = BudgetLevel.WARNING
    else:
        level = BudgetLevel.OK
    return BudgetStatus(level=level, spent_usd_micros=spent, ceiling_usd_micros=ceiling)
