"""BudgetService: implements the jobs context's BudgetGate port.

Pre-flight before any model-calling spawn, ledger write after, and the
global daily ceiling that pauses the scheduler (spec §8) — implemented
before the first model call ships, not after (spec §10 phase 4).

The "day" is the local calendar day in the configured timezone, converted
to UTC for the ledger query.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time
from zoneinfo import ZoneInfo

from atlas.budget.application.ports import BudgetLedgerRepository, PricingTable
from atlas.budget.domain.events import BudgetStatusChanged, DailyCeilingReached
from atlas.budget.domain.ledger import LedgerEntry, UsdMicros
from atlas.budget.domain.policy import BudgetLevel, BudgetStatus, evaluate
from atlas.connectors.domain.tools import TokenUsage
from atlas.jobs.application.ports import BudgetDecision
from atlas.jobs.domain.definition import JobDefinition
from atlas.shared.clock import Clock
from atlas.shared.events import InProcessEventBus
from atlas.shared.ids import RunId, new_entry_id

logger = logging.getLogger(__name__)


class BudgetService:
    def __init__(
        self,
        *,
        repo: BudgetLedgerRepository,
        pricing: PricingTable,
        bus: InProcessEventBus,
        clock: Clock,
        model: str,
        daily_ceiling: UsdMicros,
        timezone: str,
        pause_scheduler: Callable[[], None],
    ) -> None:
        self._repo = repo
        self._pricing = pricing
        self._bus = bus
        self._clock = clock
        self._model = model
        self._ceiling = daily_ceiling
        self._tz = ZoneInfo(timezone)
        self._pause_scheduler = pause_scheduler
        self._last_level: BudgetLevel | None = None

    def _day_start_utc(self) -> datetime:
        local_now = self._clock.now().astimezone(self._tz)
        midnight_local = datetime.combine(local_now.date(), time.min, tzinfo=self._tz)
        return midnight_local.astimezone(self._clock.now().tzinfo)

    async def current_status(self) -> BudgetStatus:
        spent = await self._repo.total_since(self._day_start_utc())
        return evaluate(spent, self._ceiling)

    async def preflight(self, definition: JobDefinition) -> BudgetDecision:
        status = await self.current_status()
        if status.level is BudgetLevel.EXHAUSTED:
            return BudgetDecision(allowed=False, reason="daily spend ceiling reached")
        return BudgetDecision(allowed=True)

    async def record(self, definition: JobDefinition, run_id: RunId, usage: TokenUsage) -> None:
        entry = LedgerEntry(
            entry_id=new_entry_id(),
            run_id=run_id,
            job_id=definition.id,
            model=self._model,
            usage=usage,
            cost_usd_micros=self._pricing.cost(self._model, usage),
            recorded_at=self._clock.now(),
        )
        await self._repo.add(entry)
        status = await self.current_status()
        now = self._clock.now()

        if status.level is BudgetLevel.EXHAUSTED and self._last_level is not BudgetLevel.EXHAUSTED:
            # The moment of crossing: pause the scheduler and say so loudly
            # (spec §8).
            self._pause_scheduler()
            await self._bus.publish(DailyCeilingReached(occurred_at=now, status=status))
        if status.level is not self._last_level:
            await self._bus.publish(BudgetStatusChanged(occurred_at=now, status=status))
            self._last_level = status.level
