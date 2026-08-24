"""SqliteJobRunRepository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite

from pihome.jobs.domain.run import JobRun, RunReport, RunState
from pihome.persistence.db import Database
from pihome.shared.ids import JobId, RunId


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_run(row: aiosqlite.Row) -> JobRun:
    report_json: str | None = row["report_json"]
    started_at = _from_iso(row["started_at"])
    assert started_at is not None  # NOT NULL column
    return JobRun(
        run_id=RunId(row["run_id"]),
        job_id=JobId(row["job_id"]),
        tier=row["tier"],
        mode=row["mode"],
        attempt=row["attempt"],
        state=RunState(row["state"]),
        scheduled_for=_from_iso(row["scheduled_for"]),
        started_at=started_at,
        finished_at=_from_iso(row["finished_at"]),
        report=RunReport.model_validate_json(report_json) if report_json else None,
        error=row["error"],
    )


class SqliteJobRunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, run: JobRun) -> None:
        await self._db.connection.execute(
            "INSERT INTO job_runs (run_id, job_id, tier, mode, state, attempt,"
            " scheduled_for, started_at, finished_at, report_json, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            self._params(run),
        )
        await self._db.connection.commit()

    async def update(self, run: JobRun) -> None:
        await self._db.connection.execute(
            "UPDATE job_runs SET state = ?, finished_at = ?, report_json = ?, error = ?"
            " WHERE run_id = ?",
            (
                str(run.state),
                _iso(run.finished_at),
                run.report.model_dump_json() if run.report else None,
                run.error,
                run.run_id,
            ),
        )
        await self._db.connection.commit()

    async def get(self, run_id: RunId) -> JobRun | None:
        async with self._db.connection.execute(
            "SELECT * FROM job_runs WHERE run_id = ?", (run_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_run(row) if row else None

    async def recent(self, limit: int) -> list[JobRun]:
        async with self._db.connection.execute(
            "SELECT * FROM job_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cursor:
            return [_row_to_run(row) for row in await cursor.fetchall()]

    async def recent_for_job(self, job_id: JobId, limit: int) -> list[JobRun]:
        async with self._db.connection.execute(
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        ) as cursor:
            return [_row_to_run(row) for row in await cursor.fetchall()]

    async def consecutive_failures(self, job_id: JobId) -> int:
        """Terminal runs only, newest first, counting the failed streak."""
        async with self._db.connection.execute(
            "SELECT state FROM job_runs WHERE job_id = ? AND state != 'running'"
            " ORDER BY started_at DESC LIMIT 20",
            (job_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        streak = 0
        for row in rows:
            if row["state"] in (str(RunState.FAILED), str(RunState.TIMED_OUT)):
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _params(run: JobRun) -> tuple[Any, ...]:
        return (
            run.run_id,
            run.job_id,
            run.tier,
            run.mode,
            str(run.state),
            run.attempt,
            _iso(run.scheduled_for),
            run.started_at.isoformat(),
            _iso(run.finished_at),
            run.report.model_dump_json() if run.report else None,
            run.error,
        )
