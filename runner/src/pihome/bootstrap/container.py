"""Parent composition root: wires every context's ports for `pihome serve`.

This is the one module that may import across all contexts' infrastructure
(D18); everything else depends on ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pihome.approvals.application.decide import DecideApprovalService
from pihome.approvals.application.request import RequestApprovalService
from pihome.approvals.application.sweep import ExpireSweepService
from pihome.approvals.infrastructure.payload_executor import (
    DirectWriteExecutor,
    ToolGatewayPayloadExecutor,
)
from pihome.approvals.infrastructure.sqlite_repo import SqliteApprovalRepository
from pihome.bootstrap.connectors_factory import build_connectors, build_notifier, gateway_for
from pihome.budget.application.service import BudgetService
from pihome.budget.domain.ledger import usd
from pihome.budget.infrastructure.pricing import StaticPricingTable
from pihome.budget.infrastructure.sqlite_repo import SqliteBudgetLedgerRepository
from pihome.config import Settings
from pihome.jobs.application.catalog import JobCatalog
from pihome.jobs.application.execute_job import ExecuteJobService
from pihome.jobs.application.scheduler import CronScheduler
from pihome.jobs.domain.definition import JobDefinition
from pihome.jobs.infrastructure.sqlite_run_repo import SqliteJobRunRepository
from pihome.jobs.infrastructure.subprocess_launcher import SubprocessJobLauncher
from pihome.jobs.infrastructure.yaml_source import YamlJobDefinitionSource
from pihome.persistence.db import Database
from pihome.shared.clock import SystemClock
from pihome.shared.events import InProcessEventBus
from pihome.telemetry.application.display_mode import DisplayModeTracker
from pihome.telemetry.application.health import HealthService
from pihome.telemetry.application.publisher import MqttPublisherService
from pihome.telemetry.application.stream import EventStream
from pihome.telemetry.infrastructure.mqtt_bus import AiomqttEventBus


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

    payload_executor = ToolGatewayPayloadExecutor(
        lookup=catalog.get,
        gateway_factory=lambda definition: gateway_for(definition, connectors),
    )
    write_executor = DirectWriteExecutor(
        gateway_factory=lambda definition: gateway_for(definition, connectors)
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
    )
