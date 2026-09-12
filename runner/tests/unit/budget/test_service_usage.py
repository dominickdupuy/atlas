from __future__ import annotations

from datetime import UTC, datetime

from atlas.budget.application.service import BudgetService
from atlas.budget.domain.ledger import LedgerEntry, usd
from atlas.budget.infrastructure.pricing import StaticPricingTable
from atlas.connectors.domain.tools import TokenUsage
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus
from atlas.shared.ids import JobId


class _MemoryLedger:
    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    async def add(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    async def total_since(self, since: datetime) -> int:
        return sum(int(e.cost_usd_micros) for e in self.entries)


def _service(ledger: _MemoryLedger, ceiling: str = "5.00") -> BudgetService:
    return BudgetService(
        repo=ledger,  # type: ignore[arg-type]
        pricing=StaticPricingTable(),
        bus=InProcessEventBus(),
        clock=FrozenClock(datetime(2026, 9, 12, 20, 0, tzinfo=UTC)),
        model="claude-sonnet-5",
        daily_ceiling=usd(ceiling),
        timezone="UTC",
        pause_scheduler=lambda: None,
    )


async def test_record_usage_prices_the_given_model_not_the_job_model() -> None:
    ledger = _MemoryLedger()
    service = _service(ledger)
    await service.record_usage(
        model="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=1_000_000, output_tokens=0),
        job_id=JobId("voice"),
        run_id=None,
    )
    entry = ledger.entries[0]
    assert entry.model == "claude-haiku-4-5-20251001"
    assert entry.job_id == "voice"
    assert entry.run_id is None
    assert int(entry.cost_usd_micros) == usd("0.80")


async def test_allows_reflects_the_ceiling() -> None:
    ledger = _MemoryLedger()
    service = _service(ledger, ceiling="0.50")
    assert (await service.allows()).allowed is True
    await service.record_usage(
        model="claude-sonnet-5",
        usage=TokenUsage(input_tokens=1_000_000, output_tokens=0),
        job_id=JobId("voice"),
        run_id=None,
    )
    decision = await service.allows()
    assert decision.allowed is False
    assert "ceiling" in decision.reason
