"""The board's data, assembled once and served as JSON.

Lives in presentation for the same reason PanelRenderer does: this is the
one layer D18 allows to reach across every context, and a status view that
spans jobs, approvals, budget and telemetry is a view, not a domain concept.

Everything the display shows comes from this single snapshot. One endpoint
means the board can never render half of one poll beside half of another,
and it gives the stale-data warning something honest to measure — if this
call fails, *nothing* on the screen is current, and the screen says so.

Lists are truncated with an explicit total. A board that silently shows the
first five of nineteen failures is worse than one that admits to fourteen
more, and per D11 nothing may require scrolling to reach.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from atlas.approvals.domain.approval import Approval
from atlas.bootstrap.container import Application
from atlas.budget.domain.ledger import UsdMicros, format_usd
from atlas.budget.domain.policy import BudgetLevel
from atlas.connectors.application.ports import CalendarEvent
from atlas.jobs.domain.definition import ExecutionMode, JobDefinition
from atlas.jobs.domain.run import JobRun, RunState
from atlas.telemetry.infrastructure.service_probes import ServiceStatus, probe_all
from atlas.telemetry.infrastructure.system_metrics import SystemMetrics

MAX_ALERTS = 6
MAX_APPROVALS = 5
MAX_RUNS = 8
RUN_SCAN_DEPTH = 40
"""How far back to look for failures. Deeper than the runs actually shown:
a failure that has scrolled past the visible list is still worth alerting on."""

FAILED_STATES = (RunState.FAILED, RunState.TIMED_OUT)

logger = logging.getLogger(__name__)

TIMELINE_START_HOUR = 6
TIMELINE_END_HOUR = 22
"""The board's visible band. Wider than the design's 7am-9pm because the
shipped jobs actually run 06:00-22:00, and a timeline that clips real work
to match an artboard is a lying timeline."""

TIMELINE_DAYS = 3
MAX_TIMELINE_ENTRIES = 60
RECURRING_THRESHOLD = 3
"""Above this many firings of one job in one day, the board draws a single
band with a cadence label. `*/30 6-22` is 34 firings — thirty-four unreadable
slivers say less than one bar reading "every 30m - 34 runs"."""

WEATHER_CACHE_SECONDS = 600.0


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Alert(_Frozen):
    severity: str
    kind: str
    summary: str
    detail: str | None = None
    at: datetime | None = None


class ServiceInfo(_Frozen):
    profile: str
    version: str
    revision: str
    uptime_seconds: float
    scheduler_paused: bool
    display_mode: str
    jobs_enabled: int
    jobs_total: int


class ModeSummary(_Frozen):
    """D8 is per-job, not global: there is no single switch to read. What
    governs what the system may do is how many jobs can mutate without
    asking, so that is what the board reports."""

    counts: dict[str, int]
    write_capable: list[str]


class ApprovalItem(_Frozen):
    approval_id: str
    job_id: str
    summary: str
    created_at: datetime
    expires_at: datetime


class RunItem(_Frozen):
    run_id: str
    job_id: str
    tier: int
    mode: str
    state: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    error: str | None


class ContainerItem(_Frozen):
    name: str
    endpoint: str
    reachable: bool
    latency_ms: float | None
    detail: str | None


class WifiInfo(_Frozen):
    interface: str
    link_percent: float | None
    signal_dbm: float | None


class SystemInfo(_Frozen):
    cpu_temp_c: float | None
    load_1: float | None
    load_5: float | None
    load_15: float | None
    mem_used_percent: float | None
    mem_total_bytes: int | None
    disk_used_percent: float | None
    disk_total_bytes: int | None
    uptime_seconds: float | None
    wifi: WifiInfo | None


class TimelineDay(_Frozen):
    day_offset: int
    label: str
    is_today: bool
    entry_count: int


class TimelineEntry(_Frozen):
    kind: str
    """job_run | job_scheduled | calendar_event"""

    label: str
    detail: str | None
    day_offset: int
    start_minutes: int
    """Minutes from local midnight; the board maps this onto the hour band."""

    end_minutes: int | None
    category: str
    status: str | None = None
    count: int = 1


class CalendarInfo(_Frozen):
    """Where the calendar came from and how fresh it is.

    A published feed is a third party that can lag or fail, so the board is
    told when it was last fetched rather than being left to imply the events
    are current."""

    configured: bool
    detail: str
    synced_at: datetime | None = None
    error: str | None = None
    event_count: int = 0


class WeatherInfo(_Frozen):
    available: bool
    detail: str | None = None
    summary: str | None = None
    temperature_c: float | None = None
    high_c: float | None = None
    low_c: float | None = None
    precipitation_chance_pct: int | None = None


class BudgetInfo(_Frozen):
    level: str
    spent: str
    ceiling: str
    used_percent: float


class StatusSnapshot(_Frozen):
    generated_at: datetime
    service: ServiceInfo
    modes: ModeSummary
    alerts: list[Alert]
    alerts_total: int
    approvals: list[ApprovalItem]
    approvals_total: int
    runs: list[RunItem]
    containers: list[ContainerItem]
    system: SystemInfo
    budget: BudgetInfo
    timeline_start_hour: int
    timeline_end_hour: int
    timeline_days: list[TimelineDay]
    timeline: list[TimelineEntry]
    calendar: CalendarInfo
    weather: WeatherInfo


def _duration_seconds(run: JobRun) -> float | None:
    if run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def _run_item(run: JobRun) -> RunItem:
    return RunItem(
        run_id=run.run_id,
        job_id=run.job_id,
        tier=run.tier,
        mode=run.mode,
        state=str(run.state),
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=_duration_seconds(run),
        error=run.error,
    )


def _approval_item(approval: Approval) -> ApprovalItem:
    return ApprovalItem(
        approval_id=approval.approval_id,
        job_id=approval.job_id,
        summary=approval.action.summary,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
    )


def _container_item(status: ServiceStatus) -> ContainerItem:
    return ContainerItem(
        name=status.name,
        endpoint=status.endpoint,
        reachable=status.reachable,
        latency_ms=status.latency_ms,
        detail=status.detail,
    )


def _system_info(metrics: SystemMetrics) -> SystemInfo:
    wifi = metrics.wifi
    return SystemInfo(
        cpu_temp_c=metrics.cpu_temp_c,
        load_1=metrics.load_1,
        load_5=metrics.load_5,
        load_15=metrics.load_15,
        mem_used_percent=metrics.mem_used_percent,
        mem_total_bytes=metrics.mem_total_bytes,
        disk_used_percent=metrics.disk_used_percent,
        disk_total_bytes=metrics.disk_total_bytes,
        uptime_seconds=metrics.uptime_seconds,
        wifi=(
            WifiInfo(
                interface=wifi.interface,
                link_percent=wifi.link_percent,
                signal_dbm=wifi.signal_dbm,
            )
            if wifi
            else None
        ),
    )


def build_alerts(
    runs: list[JobRun],
    containers: list[ServiceStatus],
    *,
    scheduler_paused: bool,
    budget_level: BudgetLevel,
) -> list[Alert]:
    """Ordered by what should pull the eye first: something is broken, then
    something is stopped, then something is nearly out of money."""
    alerts: list[Alert] = []

    for container in containers:
        if not container.reachable:
            alerts.append(
                Alert(
                    severity="critical",
                    kind="container_down",
                    summary=f"{container.name} unreachable",
                    detail=container.detail or container.endpoint,
                )
            )

    for run in runs:
        if run.state in FAILED_STATES:
            alerts.append(
                Alert(
                    severity="critical",
                    kind="job_failed",
                    summary=f"{run.job_id} {str(run.state).replace('_', ' ')}",
                    detail=run.error,
                    at=run.finished_at or run.started_at,
                )
            )

    if scheduler_paused:
        alerts.append(
            Alert(
                severity="warning",
                kind="scheduler_paused",
                summary="scheduler paused",
                detail="no jobs will fire until it resumes",
            )
        )

    if budget_level is BudgetLevel.EXHAUSTED:
        alerts.append(
            Alert(
                severity="warning",
                kind="budget_exhausted",
                summary="daily spend ceiling reached",
                detail="tier 2 and 3 jobs are blocked until the window rolls over",
            )
        )
    elif budget_level is BudgetLevel.WARNING:
        alerts.append(
            Alert(
                severity="warning",
                kind="budget_warning",
                summary="daily spend above 80%",
            )
        )

    return alerts


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _cadence_label(fires: list[datetime]) -> str:
    gaps = sorted(
        int((later - earlier).total_seconds() // 60) for earlier, later in pairwise(fires)
    )
    if not gaps:
        return ""
    median = gaps[len(gaps) // 2]
    if median >= 60 and median % 60 == 0:
        return f"every {median // 60}h"
    return f"every {median}m"


def _run_entry(run: JobRun, tz: ZoneInfo, today: datetime) -> TimelineEntry | None:
    started = run.started_at.astimezone(tz)
    day_offset = (started.date() - today.date()).days
    if not 0 <= day_offset < TIMELINE_DAYS:
        return None
    finished = run.finished_at.astimezone(tz) if run.finished_at else None
    detail = str(run.state).replace("_", " ")
    if finished:
        seconds = (finished - started).total_seconds()
        detail = f"{detail} · {seconds:.1f}s" if seconds < 60 else f"{detail} · {seconds / 60:.0f}m"
    return TimelineEntry(
        kind="job_run",
        label=str(run.job_id),
        detail=detail,
        day_offset=day_offset,
        start_minutes=_minutes(started),
        end_minutes=_minutes(finished) if finished else None,
        category="atlas",
        status=str(run.state),
    )


def _run_entries(runs: list[JobRun], tz: ZoneInfo, today: datetime) -> list[TimelineEntry]:
    """Collapse repetitive SUCCESS, never repetitive failure.

    A */30 job produces thirty-odd identical green bars a day, which drowns
    the one red one — the exact opposite of what the board is for (D11). So
    completed runs of the same job collapse into a single band with a count,
    and anything that failed, timed out or is awaiting approval always keeps
    its own block.
    """
    entries: list[TimelineEntry] = []
    completed: dict[tuple[str, int], list[JobRun]] = defaultdict(list)

    for run in runs:
        started = run.started_at.astimezone(tz)
        day_offset = (started.date() - today.date()).days
        if not 0 <= day_offset < TIMELINE_DAYS:
            continue
        if run.state is RunState.COMPLETED:
            completed[(str(run.job_id), day_offset)].append(run)
            continue
        entry = _run_entry(run, tz, today)
        if entry:
            entries.append(entry)

    for (job_id, day_offset), group in completed.items():
        if len(group) <= RECURRING_THRESHOLD:
            entries.extend(e for e in (_run_entry(run, tz, today) for run in group) if e)
            continue
        group.sort(key=lambda run: run.started_at)
        first = group[0].started_at.astimezone(tz)
        last = group[-1].started_at.astimezone(tz)
        entries.append(
            TimelineEntry(
                kind="job_run",
                label=job_id,
                detail=f"{len(group)} runs · all completed",
                day_offset=day_offset,
                start_minutes=_minutes(first),
                end_minutes=_minutes(last),
                category="atlas",
                status="completed",
                count=len(group),
            )
        )
    return entries


def _scheduled_entries(
    fires: list[tuple[JobDefinition, datetime]], tz: ZoneInfo, today: datetime
) -> list[TimelineEntry]:
    """Group each job's firings per day, collapsing frequent ones into a band."""
    grouped: dict[tuple[str, int], list[datetime]] = defaultdict(list)
    definitions: dict[str, JobDefinition] = {}
    for definition, fire in fires:
        local = fire.astimezone(tz)
        day_offset = (local.date() - today.date()).days
        if not 0 <= day_offset < TIMELINE_DAYS:
            continue
        grouped[(str(definition.id), day_offset)].append(local)
        definitions[str(definition.id)] = definition

    entries: list[TimelineEntry] = []
    for (job_id, day_offset), moments in grouped.items():
        moments.sort()
        definition = definitions[job_id]
        tier_mode = f"tier {int(definition.tier)} · {definition.mode}"
        if len(moments) > RECURRING_THRESHOLD:
            entries.append(
                TimelineEntry(
                    kind="job_scheduled",
                    label=job_id,
                    detail=f"{_cadence_label(moments)} · {len(moments)} runs · {tier_mode}",
                    day_offset=day_offset,
                    start_minutes=_minutes(moments[0]),
                    end_minutes=_minutes(moments[-1]),
                    category="atlas",
                    count=len(moments),
                )
            )
            continue
        for moment in moments:
            entries.append(
                TimelineEntry(
                    kind="job_scheduled",
                    label=job_id,
                    detail=f"{moment.strftime('%H:%M')} · {tier_mode}",
                    day_offset=day_offset,
                    start_minutes=_minutes(moment),
                    end_minutes=None,
                    category="atlas",
                )
            )
    return entries


def _calendar_entries(
    events: tuple[CalendarEvent, ...], tz: ZoneInfo, today: datetime
) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for event in events:
        start = event.start.astimezone(tz)
        day_offset = (start.date() - today.date()).days
        if not 0 <= day_offset < TIMELINE_DAYS:
            continue
        finish = event.end.astimezone(tz) if event.end else None
        # An event running past midnight is clamped to its own day rather than
        # bleeding into tomorrow's column, which would misreport both.
        end_minutes = None
        if finish is not None:
            end_minutes = _minutes(finish) if finish.date() == start.date() else 24 * 60 - 1
        detail = event.location or ""
        if event.all_day:
            detail = "all day" + (f" · {detail}" if detail else "")
        entries.append(
            TimelineEntry(
                kind="calendar_event",
                label=event.summary,
                detail=detail or None,
                day_offset=day_offset,
                start_minutes=_minutes(start),
                end_minutes=end_minutes,
                category="calendar",
                status="busy" if event.busy else "free",
            )
        )
    return entries


def build_timeline(
    now_local: datetime,
    runs: list[JobRun],
    fires: list[tuple[JobDefinition, datetime]],
    tz: ZoneInfo,
    calendar_events: tuple[CalendarEvent, ...] = (),
) -> tuple[list[TimelineDay], list[TimelineEntry]]:
    entries = _run_entries(runs, tz, now_local)
    entries.extend(_scheduled_entries(fires, tz, now_local))
    entries.extend(_calendar_entries(calendar_events, tz, now_local))
    entries.sort(key=lambda item: (item.day_offset, item.start_minutes))
    entries = entries[:MAX_TIMELINE_ENTRIES]

    days: list[TimelineDay] = []
    for offset in range(TIMELINE_DAYS):
        day = now_local + timedelta(days=offset)
        days.append(
            TimelineDay(
                day_offset=offset,
                label=f"{day.strftime('%a')} {day.day}",
                is_today=offset == 0,
                entry_count=sum(1 for entry in entries if entry.day_offset == offset),
            )
        )
    return days, entries


def mode_summary(application: Application) -> ModeSummary:
    counts = dict.fromkeys((str(mode) for mode in ExecutionMode), 0)
    write_capable: list[str] = []
    for definition in application.catalog.enabled_jobs:
        counts[str(definition.mode)] += 1
        if definition.mode is ExecutionMode.WRITE and definition.auto_approve:
            write_capable.append(str(definition.id))
    return ModeSummary(counts=counts, write_capable=write_capable)


class StatusAssembler:
    def __init__(self, application: Application) -> None:
        self._app = application
        self._tz = ZoneInfo(application.settings.tz)
        self._weather_cache: tuple[datetime, WeatherInfo] | None = None

    async def _calendar(
        self, window_start: datetime, window_end: datetime
    ) -> tuple[CalendarInfo, tuple[CalendarEvent, ...]]:
        if self._app.calendar is None:
            return (
                CalendarInfo(
                    configured=False,
                    detail="no calendar feed configured (set ATLAS_CALENDAR_ICS_URL)",
                ),
                (),
            )
        feed = await self._app.calendar.get_events(window_start, window_end)
        detail = "published .ics feed"
        if feed.error and not feed.events:
            detail = f"unavailable: {feed.error}"
        elif feed.error:
            detail = "published .ics feed · last refresh failed"
        return (
            CalendarInfo(
                configured=True,
                detail=detail,
                synced_at=feed.fetched_at,
                error=feed.error,
                event_count=len(feed.events),
            ),
            feed.events,
        )

    async def _weather(self, now: datetime) -> WeatherInfo:
        settings = self._app.settings
        if settings.profile != "prod":
            # StubWeather returns a canned 21C. Reporting that as the weather
            # on a wall display is worse than reporting nothing.
            return WeatherInfo(available=False, detail="stub connector active (ATLAS_PROFILE=dev)")
        cached = self._weather_cache
        if cached and (now - cached[0]).total_seconds() < WEATHER_CACHE_SECONDS:
            return cached[1]
        try:
            forecast = await self._app.weather.get_forecast(
                settings.weather_lat, settings.weather_lon
            )
        except Exception as exc:
            logger.warning("weather unavailable: %s", exc)
            return WeatherInfo(available=False, detail=f"unavailable: {exc}")
        info = WeatherInfo(
            available=True,
            summary=forecast.summary,
            temperature_c=forecast.temperature_c,
            high_c=forecast.high_c,
            low_c=forecast.low_c,
            precipitation_chance_pct=forecast.precipitation_chance_pct,
        )
        self._weather_cache = (now, info)
        return info

    async def snapshot(self) -> StatusSnapshot:
        app = self._app
        now = app.clock.now()

        recent = await app.run_repo.recent(RUN_SCAN_DEPTH)
        pending = await app.approval_repo.pending()
        budget_status = await app.budget.current_status()
        containers = await probe_all(app.probes)
        metrics = app.metrics.read()

        now_local = now.astimezone(self._tz)
        window_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=TIMELINE_DAYS)
        calendar, calendar_events = await self._calendar(window_start, window_end)
        fires = app.scheduler.occurrences_between(now, now + timedelta(days=TIMELINE_DAYS))
        timeline_days, timeline = build_timeline(
            now_local, recent, fires, self._tz, calendar_events
        )
        weather = await self._weather(now)

        alerts = build_alerts(
            recent,
            containers,
            scheduler_paused=app.scheduler.paused,
            budget_level=budget_status.level,
        )
        ceiling = UsdMicros(budget_status.ceiling_usd_micros)
        spent = UsdMicros(budget_status.spent_usd_micros)

        return StatusSnapshot(
            generated_at=now,
            service=ServiceInfo(
                profile=app.settings.profile,
                version=app.version,
                revision=app.revision,
                uptime_seconds=(now - app.started_at).total_seconds(),
                scheduler_paused=app.scheduler.paused,
                display_mode=app.display_mode.mode,
                jobs_enabled=len(app.catalog.enabled_jobs),
                jobs_total=len(app.catalog.all_jobs),
            ),
            modes=mode_summary(app),
            alerts=alerts[:MAX_ALERTS],
            alerts_total=len(alerts),
            approvals=[_approval_item(approval) for approval in pending[:MAX_APPROVALS]],
            approvals_total=len(pending),
            runs=[_run_item(run) for run in recent[:MAX_RUNS]],
            containers=[_container_item(status) for status in containers],
            system=_system_info(metrics),
            budget=BudgetInfo(
                level=str(budget_status.level),
                spent=format_usd(spent),
                ceiling=format_usd(ceiling),
                used_percent=100.0 * spent / ceiling if ceiling else 0.0,
            ),
            timeline_start_hour=TIMELINE_START_HOUR,
            timeline_end_hour=TIMELINE_END_HOUR,
            timeline_days=timeline_days,
            timeline=timeline,
            calendar=calendar,
            weather=weather,
        )
