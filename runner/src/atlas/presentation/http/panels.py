"""The board's panels: named render functions shared by the initial page
load, the /partials refresh endpoints, and the SSE stream — one rendering
path, three delivery mechanisms.

Every SSE update re-renders its panel from live state rather than patching
the DOM incrementally: on a ~six-panel board, correctness beats cleverness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

from atlas.approvals.domain.events import ApprovalDecided, ApprovalExpired, ApprovalRequested
from atlas.bootstrap.container import Application
from atlas.budget.domain.events import BudgetStatusChanged, DailyCeilingReached
from atlas.budget.domain.ledger import format_usd
from atlas.jobs.domain.events import JobRunEvent
from atlas.jobs.domain.run import JobRun, RunState
from atlas.shared.events import DomainEvent
from atlas.telemetry.domain.topics import DisplayModeChanged, SystemHealth
from atlas.telemetry.infrastructure.hosted_repos import HostedRepoRun

PANEL_NAMES = ("mode", "jobs", "approvals", "budget", "schedule", "health")

MAX_PENDING = 6
MAX_HISTORY = 8
"""Caps for the Runs timeline. Truncation is reported with an explicit total
for the same reason status.py does it: a panel that silently shows six of
nineteen is worse than one admitting to thirteen more."""


@dataclass(frozen=True)
class TimelineEntry:
    """One row of the Runs panel.

    Two systems put work on this Pi -- atlas jobs (LLM-driven, in the runner)
    and hosted repos (ordinary checkouts driven by cron, see docs/repos.md).
    `origin` keeps them labelled so a red row is traceable to the thing that
    can actually be fixed.
    """

    origin: str
    name: str
    state: str
    when: datetime | None
    detail: str = ""

    @property
    def pending(self) -> bool:
        return self.state in ("queued", RunState.AWAITING_APPROVAL.value)


def panels_for_event(event: DomainEvent) -> tuple[str, ...]:
    match event:
        case JobRunEvent():
            return ("jobs", "schedule")
        case ApprovalRequested() | ApprovalDecided() | ApprovalExpired():
            return ("approvals", "jobs")
        case BudgetStatusChanged() | DailyCeilingReached():
            return ("budget", "health")
        case DisplayModeChanged():
            return ("mode",)
        case SystemHealth():
            return ("health",)
        case _:
            return ()


class PanelRenderer:
    def __init__(self, application: Application) -> None:
        self._app = application
        self._tz = ZoneInfo(application.settings.tz)
        self._env = Environment(
            loader=PackageLoader("atlas.presentation", "templates"),
            autoescape=select_autoescape(["html"]),
        )
        self._env.filters["localtime"] = self._localtime

    def _localtime(self, value: datetime | None) -> str:
        if value is None:
            return "—"
        return value.astimezone(self._tz).strftime("%a %H:%M")

    def _timeline_context(self, runs: list[JobRun]) -> dict[str, object]:
        """Queued work above, history below, both systems in one table.

        Awaiting-approval runs sort into the pending group rather than the
        history: the job has not finished, it is blocked on a person, and
        burying it under later-but-completed rows is how it gets forgotten.
        """
        now = self._app.clock.now()

        pending = [
            TimelineEntry(
                origin="atlas",
                name=run.job_id,
                state=run.state.value,
                when=run.started_at,
                detail="waiting on approval",
            )
            for run in runs
            if run.state is RunState.AWAITING_APPROVAL
        ]
        pending += [
            TimelineEntry(
                origin="atlas",
                name=job.id,
                state="queued",
                when=fire_at,
                detail=f"tier {job.tier.value} · {job.mode.value}",
            )
            for job, fire_at in self._app.scheduler.next_fires()[:MAX_PENDING]
        ]
        pending += [
            TimelineEntry(
                origin="repo",
                name=fire.name,
                state="queued",
                when=fire.at,
                detail=fire.note or fire.source,
            )
            for fire in self._app.hosted_repos.upcoming(now)
        ]
        pending.sort(key=lambda entry: (entry.when is None, entry.when))

        history = [
            TimelineEntry(
                origin="atlas",
                name=run.job_id,
                state=run.state.value,
                when=run.started_at,
                detail=(run.error or "")[:80],
            )
            for run in runs
            if run.state is not RunState.AWAITING_APPROVAL
        ]
        history += [
            TimelineEntry(
                origin="repo",
                name=repo_run.name,
                state="failed" if repo_run.failed else "completed",
                when=repo_run.started,
                detail=self._repo_detail(repo_run),
            )
            for repo_run in self._app.hosted_repos.last_runs()
        ]
        history.sort(key=lambda entry: (entry.when is not None, entry.when), reverse=True)

        return {
            "pending": pending[:MAX_PENDING],
            "pending_more": max(0, len(pending) - MAX_PENDING),
            "history": history[:MAX_HISTORY],
            "history_more": max(0, len(history) - MAX_HISTORY),
        }

    @staticmethod
    def _repo_detail(run: HostedRepoRun) -> str:
        parts = [run.trigger] if run.trigger else []
        if run.duration_seconds is not None:
            parts.append(f"{run.duration_seconds:.0f}s")
        if run.failed and run.exit_code is not None:
            parts.append(f"exit {run.exit_code}")
        return " · ".join(parts)

    async def render(self, panel: str) -> str:
        context: dict[str, object]
        match panel:
            case "mode":
                context = {"mode": self._app.display_mode.mode}
            case "jobs":
                context = self._timeline_context(await self._app.run_repo.recent(20))
            case "approvals":
                context = {"approvals": await self._app.approval_repo.pending()}
            case "budget":
                context = {
                    "status": await self._app.budget.current_status(),
                    "format_usd": format_usd,
                }
            case "schedule":
                context = {"fires": self._app.scheduler.next_fires()[:8]}
            case "health":
                context = {
                    "paused": self._app.scheduler.paused,
                    "job_count": len(self._app.catalog.enabled_jobs),
                    "sse_clients": self._app.stream.client_count,
                    "profile": self._app.settings.profile,
                }
            case _:
                raise KeyError(f"unknown panel {panel!r}")
        return self._env.get_template(f"partials/{panel}.html").render(**context)

    async def render_page(self) -> str:
        rendered = {name: await self.render(name) for name in PANEL_NAMES}
        return self._env.get_template("dashboard.html").render(panels=rendered)

    def render_board(self) -> str:
        """The passive ops board (D11). A near-static shell: it carries no
        server-rendered state, because everything it draws arrives from one
        /api/status poll. That is what lets it keep the last good screen up,
        and label it as stale, when the API stops answering.

        The one thing rendered in is the asset version. StaticFiles sends
        ETag and Last-Modified but no Cache-Control, so a browser may reuse a
        cached stylesheet heuristically without revalidating — which after a
        deploy pairs new markup with old CSS. On a kiosk nobody reloads by
        hand, that is permanent until someone notices the screen is wrong.
        """
        return self._env.get_template("board.html").render(
            asset_version=f"{self._app.version}-{self._app.revision}"
        )
