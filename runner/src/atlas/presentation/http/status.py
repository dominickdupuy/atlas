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

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from atlas.approvals.domain.approval import Approval
from atlas.bootstrap.container import Application
from atlas.budget.domain.ledger import UsdMicros, format_usd
from atlas.budget.domain.policy import BudgetLevel
from atlas.jobs.domain.definition import ExecutionMode
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
                    summary=f"{run.job_id} {run.state}",
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

    async def snapshot(self) -> StatusSnapshot:
        app = self._app
        now = app.clock.now()

        recent = await app.run_repo.recent(RUN_SCAN_DEPTH)
        pending = await app.approval_repo.pending()
        budget_status = await app.budget.current_status()
        containers = await probe_all(app.probes)
        metrics = app.metrics.read()

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
        )
