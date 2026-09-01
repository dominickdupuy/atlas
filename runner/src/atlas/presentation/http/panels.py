"""The board's panels: named render functions shared by the initial page
load, the /partials refresh endpoints, and the SSE stream — one rendering
path, three delivery mechanisms.

Every SSE update re-renders its panel from live state rather than patching
the DOM incrementally: on a ~six-panel board, correctness beats cleverness.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

from atlas.approvals.domain.events import ApprovalDecided, ApprovalExpired, ApprovalRequested
from atlas.bootstrap.container import Application
from atlas.budget.domain.events import BudgetStatusChanged, DailyCeilingReached
from atlas.budget.domain.ledger import format_usd
from atlas.jobs.domain.events import JobRunEvent
from atlas.shared.events import DomainEvent
from atlas.telemetry.domain.topics import DisplayModeChanged, SystemHealth

PANEL_NAMES = ("mode", "jobs", "approvals", "budget", "schedule", "health")


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

    async def render(self, panel: str) -> str:
        context: dict[str, object]
        match panel:
            case "mode":
                context = {"mode": self._app.display_mode.mode}
            case "jobs":
                context = {"runs": await self._app.run_repo.recent(10)}
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
