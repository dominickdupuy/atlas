"""Ports of the budget context."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from atlas.budget.domain.ledger import LedgerEntry, UsdMicros
from atlas.connectors.domain.tools import TokenUsage


class BudgetLedgerRepository(Protocol):
    async def add(self, entry: LedgerEntry) -> None: ...

    async def total_since(self, since: datetime) -> UsdMicros: ...


class PricingTable(Protocol):
    def cost(self, model: str, usage: TokenUsage) -> UsdMicros: ...
