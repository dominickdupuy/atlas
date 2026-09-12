"""Parent composition root: wires every context's ports for `atlas serve`.

This is the one module that may import across all contexts' infrastructure
(D18); everything else depends on ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atlas.approvals.application.decide import DecideApprovalService
from atlas.approvals.application.request import RequestApprovalService
from atlas.approvals.application.sweep import ExpireSweepService
from atlas.approvals.infrastructure.payload_executor import (
    DirectWriteExecutor,
    ToolGatewayPayloadExecutor,
)
from atlas.approvals.infrastructure.sqlite_repo import SqliteApprovalRepository
from atlas.bootstrap.connectors_factory import build_connectors, build_notifier, gateway_for
from atlas.bootstrap.lights_factory import build_lights, matter_probe
from atlas.bootstrap.voice_factory import build_voice
from atlas.budget.application.service import BudgetService
from atlas.budget.domain.ledger import usd
from atlas.budget.infrastructure.pricing import StaticPricingTable
from atlas.budget.infrastructure.sqlite_repo import SqliteBudgetLedgerRepository
from atlas.config import Settings
from atlas.connectors.application.ports import CalendarPort, WeatherPort, WeatherReportPort
from atlas.connectors.infrastructure.ics_calendar import IcsCalendarClient
from atlas.connectors.infrastructure.weather_http import OpenMeteoForecastClient
from atlas.jobs.application.catalog import JobCatalog
from atlas.jobs.application.execute_job import ExecuteJobService
from atlas.jobs.application.scheduler import CronScheduler
from atlas.jobs.domain.definition import JobDefinition
from atlas.jobs.infrastructure.sqlite_run_repo import SqliteJobRunRepository
from atlas.jobs.infrastructure.subprocess_launcher import SubprocessJobLauncher
from atlas.jobs.infrastructure.yaml_source import YamlJobDefinitionSource
from atlas.lights.application.service import LightsService
from atlas.lights.application.tools import LightsTools
from atlas.lights.infrastructure.matter_ws import MatterWsClient
from atlas.persistence.db import Database
from atlas.shared.build_info import git_revision, package_version
from atlas.shared.clock import Clock, SystemClock
from atlas.shared.events import InProcessEventBus
from atlas.telemetry.application.display_mode import DisplayModeTracker
from atlas.telemetry.application.health import HealthService
from atlas.telemetry.application.publisher import MqttPublisherService
from atlas.telemetry.application.stream import EventStream
from atlas.telemetry.infrastructure.health_board import HealthBoardReader
from atlas.telemetry.infrastructure.hosted_repos import HostedRepoReader
from atlas.telemetry.infrastructure.mqtt_bus import AiomqttEventBus
from atlas.telemetry.infrastructure.service_probes import TcpServiceProbe
from atlas.telemetry.infrastructure.system_metrics import SystemMetricsReader
from atlas.voice.application.service import VoiceService


@dataclass
class Application:
    settings: Settings
    db: Database
    bus: InProcessEventBus
    stream: EventStream
    catalog: JobCatalog
    scheduler: CronScheduler
    run_repo: SqliteJobRunRepository
    approval_repo: SqliteApprovalRepository
    budget: BudgetService
    execute_job: ExecuteJobService
    request_approval: RequestApprovalService
    decide_approval: DecideApprovalService
    sweep: ExpireSweepService
    health: HealthService
    mqtt: AiomqttEventBus
    display_mode: DisplayModeTracker
    clock: Clock
    weather: WeatherPort
    weather_report: WeatherReportPort
    calendar: CalendarPort | None
    metrics: SystemMetricsReader
    probes: tuple[TcpServiceProbe, ...]
    hosted_repos: HostedRepoReader
    health_board: HealthBoardReader
    lights: LightsService | None
    matter: MatterWsClient | None
    voice: VoiceService | None
    started_at: datetime
    version: str
    revision: str

    async def start_persistence(self) -> None:
        """Connect + migrate, then seed derived state that lives in the DB."""
        await self.db.connect()
        await self.db.migrate()
        self.display_mode.seed_pending(len(await self.approval_repo.pending()))


def build_application(settings: Settings) -> Application:
    clock = SystemClock()
    bus = InProcessEventBus()
    stream = EventStream(bus)
    db = Database(settings.db_path)

    connectors = build_connectors(settings)
    notifier = build_notifier(settings)

    catalog = JobCatalog(YamlJobDefinitionSource(settings.jobs_dir))
    catalog.load()

    run_repo = SqliteJobRunRepository(db)
    approval_repo = SqliteApprovalRepository(db)
    ledger_repo = SqliteBudgetLedgerRepository(db)

    lights, matter = build_lights(settings, bus, clock)
    lights_tools = LightsTools(lights) if lights else None

    payload_executor = ToolGatewayPayloadExecutor(
        lookup=catalog.get,
        gateway_factory=lambda definition: gateway_for(definition, connectors, lights=lights_tools),
    )
    write_executor = DirectWriteExecutor(
        gateway_factory=lambda definition: gateway_for(definition, connectors, lights=lights_tools)
    )

    # Budget needs to pause the scheduler; the scheduler needs the execute
    # service, which needs budget. The holder breaks the construction cycle
    # without making either depend on the other's concrete type.
    scheduler_holder: list[CronScheduler] = []

    def pause_scheduler() -> None:
        if scheduler_holder:
            scheduler_holder[0].pause()

    pricing = StaticPricingTable(
        input_per_mtok=(
            usd(settings.price_input_per_mtok) if settings.price_input_per_mtok else None
        ),
        output_per_mtok=(
            usd(settings.price_output_per_mtok) if settings.price_output_per_mtok else None
        ),
    )
    budget = BudgetService(
        repo=ledger_repo,
        pricing=pricing,
        bus=bus,
        clock=clock,
        model=settings.model,
        daily_ceiling=settings.daily_ceiling,
        timezone=settings.tz,
        pause_scheduler=pause_scheduler,
    )

    request_approval = RequestApprovalService(
        repo=approval_repo,
        notifier=notifier,
        bus=bus,
        clock=clock,
        public_url=settings.public_url,
        api_token=settings.api_token,
    )
    decide_approval = DecideApprovalService(
        repo=approval_repo, executor=payload_executor, bus=bus, clock=clock
    )
    sweep = ExpireSweepService(repo=approval_repo, bus=bus, clock=clock)

    execute_job = ExecuteJobService(
        repo=run_repo,
        launcher=SubprocessJobLauncher(),
        budget=budget,
        approvals=request_approval,
        writes=write_executor,
        bus=bus,
        clock=clock,
    )

    async def on_due(definition: JobDefinition, scheduled_for: datetime) -> None:
        await execute_job.execute(definition, scheduled_for)

    scheduler = CronScheduler(catalog=catalog, clock=clock, on_due=on_due, timezone=settings.tz)
    scheduler_holder.append(scheduler)

    # Gated on the URL, not on the profile: the feed needs no credentials,
    # so it works in dev exactly as it does in prod.
    calendar = (
        IcsCalendarClient(settings.calendar_ics_url, clock=clock)
        if settings.calendar_ics_url
        else None
    )

    voice = build_voice(
        settings, lights=lights, connectors=connectors, budget=budget, db=db, clock=clock
    )

    probe_list = [
        TcpServiceProbe("homeassistant", settings.homeassistant_host, settings.homeassistant_port),
        TcpServiceProbe("mosquitto", settings.mqtt_host, settings.mqtt_port),
    ]
    matter_service_probe = matter_probe(settings)
    if matter_service_probe is not None:
        probe_list.append(matter_service_probe)
    probes = tuple(probe_list)

    mqtt = AiomqttEventBus(settings.mqtt_host, settings.mqtt_port)
    MqttPublisherService(bus, mqtt)
    health = HealthService(bus=bus, clock=clock)
    display_mode = DisplayModeTracker(bus, clock)

    return Application(
        settings=settings,
        db=db,
        bus=bus,
        stream=stream,
        catalog=catalog,
        scheduler=scheduler,
        run_repo=run_repo,
        approval_repo=approval_repo,
        budget=budget,
        execute_job=execute_job,
        request_approval=request_approval,
        decide_approval=decide_approval,
        sweep=sweep,
        health=health,
        mqtt=mqtt,
        display_mode=display_mode,
        clock=clock,
        weather=connectors.weather,
        # Not profile-gated: Open-Meteo needs no key, so the board shows real
        # weather in dev exactly as in prod (same reasoning as the calendar).
        weather_report=OpenMeteoForecastClient(clock=clock),
        calendar=calendar,
        metrics=SystemMetricsReader(),
        probes=probes,
        hosted_repos=HostedRepoReader(
            settings.repos_registry, settings.repos_state_dir, settings.tz
        ),
        health_board=HealthBoardReader(settings.health_board_path, settings.tz),
        lights=lights,
        matter=matter,
        voice=voice,
        started_at=clock.now(),
        version=package_version(),
        revision=git_revision(),
    )
