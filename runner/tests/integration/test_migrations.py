"""Migration runner: ordered, recorded, idempotent."""

from __future__ import annotations

from pathlib import Path

from atlas.persistence.db import Database


async def test_migrate_applies_and_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    await db.connect()
    try:
        first = await db.migrate()
        assert first == ["0001_initial.sql"]
        second = await db.migrate()
        assert second == []

        status = await db.migration_status()
        assert status[0][0] == 1
        assert status[0][2] is not None  # applied_at recorded
    finally:
        await db.close()


async def test_schema_has_the_three_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    await db.connect()
    try:
        await db.migrate()
        async with db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ) as cursor:
            tables = {row["name"] for row in await cursor.fetchall()}
        assert {"job_runs", "approvals", "budget_ledger", "schema_migrations"} <= tables
    finally:
        await db.close()
