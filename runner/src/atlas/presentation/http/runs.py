"""One Runs timeline shared by the JSON board and the htmx panels."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from atlas.bootstrap.container import Application
from atlas.jobs.domain.run import JobRun, RunState
from atlas.telemetry.infrastructure.hosted_repos import HostedRepoRun

MAX_PENDING = 6
MAX_HISTORY = 8
_ACTIVE = (RunState.RUNNING, RunState.AWAITING_APPROVAL)


@dataclass(frozen=True)
class RunEntry:
    origin: str
    name: str
    state: str
    when: datetime | None
    detail: str = ""


@dataclass(frozen=True)
class RunsTimeline:
    pending: tuple[RunEntry, ...]
    history: tuple[RunEntry, ...]
    pending_total: int
    history_total: int

    @property
    def pending_more(self) -> int:
        return self.pending_total - len(self.pending)

    @property
    def history_more(self) -> int:
        return self.history_total - len(self.history)


def _detail(*parts: str) -> str:
    return " · ".join(part for part in parts if part)


# What a hosted job's summary figures are called on the board, in the order
# they read best. Keys a job reports that are not listed here stay in the API
# and off the screen.
_FIGURE_LABELS = (
    ("transactions", "{n:,} transactions"),
    ("reviewed", "{n:,} reviewed"),
    ("uncategorized", "{n:,} uncategorized"),
    ("decisions", "{n} decisions"),
    ("rules", "{n} new rules"),
)


def _repo_detail(run: HostedRepoRun) -> str:
    parts = [run.trigger] if run.trigger else []
    if run.duration_seconds is not None:
        parts.append(f"{run.duration_seconds:.0f}s")
    if run.failed and run.exit_code is not None:
        parts.append(f"exit {run.exit_code}")
    parts += [
        label.format(n=run.summary[key]) for key, label in _FIGURE_LABELS if key in run.summary
    ]
    return " · ".join(parts)


def _collapse(history: list[RunEntry]) -> list[RunEntry]:
    """A half-hourly job would otherwise fill the whole panel with one name
    and push every other origin off the screen. Consecutive runs of the same
    job that ended the same way become one row carrying a repeat count; a
    different outcome breaks the streak, so a failure never hides inside one.
    """
    collapsed: list[tuple[RunEntry, int]] = []
    for entry in history:
        if collapsed:
            latest, repeats = collapsed[-1]
            if (latest.origin, latest.name, latest.state) == (
                entry.origin,
                entry.name,
                entry.state,
            ):
                collapsed[-1] = (latest, repeats + 1)
                continue
        collapsed.append((entry, 1))
    return [
        entry if repeats == 1 else replace(entry, detail=_detail(entry.detail, f"{repeats} runs"))
        for entry, repeats in collapsed
    ]


def build_runs_timeline(
    app: Application, runs: list[JobRun], now: datetime, *, stub_jobs: bool = False
) -> RunsTimeline:
    # In a stub profile a run talked to canned data, which is worth saying on
    # every row: the board shows the work either way, it just does not let a
    # rehearsal read as the real thing.
    stub = "stub" if stub_jobs else ""
    # History belongs to jobs that still exist. A retired definition leaves
    # hundreds of rows behind in SQLite; drawing them would keep a job on the
    # board indefinitely after the person removed it.
    known = {str(job.id) for job in app.catalog.all_jobs}
    runs = [run for run in runs if str(run.job_id) in known]
    pending = [
        RunEntry(
            origin="atlas",
            name=run.job_id,
            state=run.state.value,
            when=run.started_at,
            detail=_detail(
                "waiting on approval" if run.state is RunState.AWAITING_APPROVAL else "running",
                stub,
            ),
        )
        for run in runs
        if run.state in _ACTIVE
    ]
    pending += [
        RunEntry(
            origin="atlas",
            name=job.id,
            state="queued",
            when=fire_at,
            detail=_detail(f"tier {job.tier.value} · {job.mode.value}", stub),
        )
        for job, fire_at in app.scheduler.next_fires()
    ]
    pending += [
        RunEntry(
            origin="repo",
            name=fire.name,
            state="queued",
            when=fire.at,
            detail=fire.note or fire.source,
        )
        for fire in app.hosted_repos.upcoming(now)
    ]
    history = [
        RunEntry(
            origin="atlas",
            name=run.job_id,
            state=run.state.value,
            when=run.started_at,
            detail=_detail((run.error or "")[:80], stub),
        )
        for run in runs
        if run.state not in _ACTIVE
    ]
    for run in app.hosted_repos.last_runs():
        state = "running" if run.status == "running" else "failed" if run.failed else "completed"
        entry = RunEntry(
            origin="repo", name=run.name, state=state, when=run.started, detail=_repo_detail(run)
        )
        (pending if state == "running" else history).append(entry)

    # Work already underway or blocked on a person precedes predicted fires.
    pending.sort(key=lambda entry: (entry.state == "queued", entry.when is None, entry.when))
    history.sort(key=lambda entry: (entry.when is not None, entry.when), reverse=True)
    collapsed = _collapse(history)
    return RunsTimeline(
        pending=tuple(pending[:MAX_PENDING]),
        history=tuple(collapsed[:MAX_HISTORY]),
        pending_total=len(pending),
        history_total=len(collapsed),
    )
