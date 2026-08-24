"""SqliteApprovalRepository. The idempotent transition is a conditional
UPDATE checked by rowcount — the storage-level enforcement of D16."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from pihome.approvals.domain.approval import Approval, ApprovalState
from pihome.jobs.domain.run import ProposedAction
from pihome.persistence.db import Database
from pihome.shared.ids import ApprovalId, JobId, RunId


def _row_to_approval(row: aiosqlite.Row) -> Approval:
    return Approval(
        approval_id=ApprovalId(row["approval_id"]),
        run_id=RunId(row["run_id"]),
        job_id=JobId(row["job_id"]),
        action=ProposedAction.model_validate_json(row["action_json"]),
        state=ApprovalState(row["state"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        decision_source=row["decision_source"],
    )


class SqliteApprovalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, approval: Approval) -> None:
        await self._db.connection.execute(
            "INSERT INTO approvals (approval_id, run_id, job_id, action_json, state,"
            " created_at, expires_at, decided_at, decision_source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval.approval_id,
                approval.run_id,
                approval.job_id,
                approval.action.model_dump_json(),
                str(approval.state),
                approval.created_at.isoformat(),
                approval.expires_at.isoformat(),
                approval.decided_at.isoformat() if approval.decided_at else None,
                approval.decision_source,
            ),
        )
        await self._db.connection.commit()

    async def get(self, approval_id: ApprovalId) -> Approval | None:
        async with self._db.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_approval(row) if row else None

    async def pending(self) -> list[Approval]:
        async with self._db.connection.execute(
            "SELECT * FROM approvals WHERE state = 'pending' ORDER BY created_at"
        ) as cursor:
            return [_row_to_approval(row) for row in await cursor.fetchall()]

    async def pending_due(self, now: datetime) -> list[Approval]:
        async with self._db.connection.execute(
            "SELECT * FROM approvals WHERE state = 'pending' AND expires_at <= ?",
            (now.isoformat(),),
        ) as cursor:
            return [_row_to_approval(row) for row in await cursor.fetchall()]

    async def transition(self, approval: Approval, expected: ApprovalState) -> bool:
        cursor = await self._db.connection.execute(
            "UPDATE approvals SET state = ?, decided_at = ?, decision_source = ?"
            " WHERE approval_id = ? AND state = ?",
            (
                str(approval.state),
                approval.decided_at.isoformat() if approval.decided_at else None,
                approval.decision_source,
                approval.approval_id,
                str(expected),
            ),
        )
        await self._db.connection.commit()
        return cursor.rowcount == 1
