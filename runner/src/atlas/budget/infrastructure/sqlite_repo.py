"""SqliteBudgetLedgerRepository."""

from __future__ import annotations

from datetime import datetime

from atlas.budget.domain.ledger import LedgerEntry, UsdMicros
from atlas.persistence.db import Database


class SqliteBudgetLedgerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, entry: LedgerEntry) -> None:
        await self._db.connection.execute(
            "INSERT INTO budget_ledger (entry_id, run_id, job_id, model,"
            " input_tokens, output_tokens, cost_usd_micros, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.entry_id,
                entry.run_id,
                entry.job_id,
                entry.model,
                entry.usage.input_tokens,
                entry.usage.output_tokens,
                int(entry.cost_usd_micros),
                entry.recorded_at.isoformat(),
            ),
        )
        await self._db.connection.commit()

    async def total_since(self, since: datetime) -> UsdMicros:
        async with self._db.connection.execute(
            "SELECT COALESCE(SUM(cost_usd_micros), 0) AS total FROM budget_ledger"
            " WHERE recorded_at >= ?",
            (since.isoformat(),),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return UsdMicros(int(row["total"]))
