"""Database: connection factory plus a numbered-SQL migration runner.

One connection, one writer (the parent process — children never open the
DB, D14 protocol). aiosqlite serializes access on its worker thread, so a
single shared connection is also the concurrency story.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def _load_migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql), sorted. Migrations ship as package data."""
    found: list[tuple[int, str, str]] = []
    package = resources.files("pihome.persistence") / "migrations"
    for entry in package.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    if not found:
        raise RuntimeError("no migrations found in pihome.persistence.migrations")
    return found


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._conn

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def migrate(self) -> list[str]:
        """Apply pending migrations in order; returns the names applied.
        Runs at startup and via `pihome migrate`."""
        conn = self.connection
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        await conn.commit()

        applied: list[str] = []
        async with conn.execute("SELECT version FROM schema_migrations") as cursor:
            done = {row["version"] for row in await cursor.fetchall()}

        for version, name, sql in _load_migrations():
            if version in done:
                continue
            logger.info("applying migration %s", name)
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).isoformat()),
            )
            await conn.commit()
            applied.append(name)
        return applied

    async def migration_status(self) -> list[tuple[int, str, str | None]]:
        """(version, name, applied_at | None) for every known migration."""
        conn = self.connection
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        async with conn.execute("SELECT version, applied_at FROM schema_migrations") as cursor:
            applied = {row["version"]: row["applied_at"] for row in await cursor.fetchall()}
        return [(version, name, applied.get(version)) for version, name, _ in _load_migrations()]
