from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.persistence.db import Database
from atlas.voice.application.ports import UtteranceRecord
from atlas.voice.domain.intent import UNKNOWN_INTENT, Intent, IntentKind
from atlas.voice.infrastructure.sqlite_log import SqliteUtteranceLog


async def test_add_and_recent_with_tier_filter(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    await db.connect()
    await db.migrate()
    log = SqliteUtteranceLog(db)
    base = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    for index, (tier, model) in enumerate([(1, None), (2, "claude-haiku-4-5-20251001"), (1, None)]):
        intent = (
            Intent(intent=IntentKind.APPLY_SCENE, scene="night") if tier == 2 else UNKNOWN_INTENT
        )
        await log.add(
            UtteranceRecord(
                id=f"u{index}",
                heard_at=base + timedelta(seconds=index),
                text=f"utterance {index}",
                tier=tier,
                model=model,
                intent=intent,
                outcome="applied" if tier == 2 else "unknown",
            )
        )
    newest_first = await log.recent(limit=10)
    assert [r.id for r in newest_first] == ["u2", "u1", "u0"]
    tier2 = await log.recent(limit=10, tier=2)
    assert [r.id for r in tier2] == ["u1"]
    assert tier2[0].model == "claude-haiku-4-5-20251001"
    assert tier2[0].intent.scene == "night"
    assert tier2[0].heard_at.tzinfo is not None
    await db.close()
