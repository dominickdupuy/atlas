"""SqliteUtteranceLog: append-only. There is deliberately no delete."""

from __future__ import annotations

from datetime import datetime

from atlas.persistence.db import Database
from atlas.voice.application.ports import UtteranceRecord
from atlas.voice.domain.intent import Intent


class SqliteUtteranceLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, record: UtteranceRecord) -> None:
        await self._db.connection.execute(
            "INSERT INTO voice_utterances (id, heard_at, text, tier, model, intent_json, outcome)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.heard_at.isoformat(),
                record.text,
                record.tier,
                record.model,
                record.intent.model_dump_json(exclude_none=True),
                record.outcome,
            ),
        )
        await self._db.connection.commit()

    async def recent(self, *, limit: int, tier: int | None = None) -> list[UtteranceRecord]:
        sql = "SELECT id, heard_at, text, tier, model, intent_json, outcome FROM voice_utterances"
        params: tuple[object, ...] = ()
        if tier is not None:
            sql += " WHERE tier = ?"
            params = (tier,)
        sql += " ORDER BY heard_at DESC LIMIT ?"
        async with self._db.connection.execute(sql, (*params, limit)) as cursor:
            rows = await cursor.fetchall()
        return [
            UtteranceRecord(
                id=row["id"],
                heard_at=datetime.fromisoformat(row["heard_at"]),
                text=row["text"],
                tier=int(row["tier"]),
                model=row["model"],
                intent=Intent.model_validate_json(row["intent_json"]),
                outcome=row["outcome"],
            )
            for row in rows
        ]
