from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from atlas.bootstrap.connectors_factory import build_connectors
from atlas.bootstrap.voice_factory import build_voice
from atlas.budget.application.service import BudgetService
from atlas.budget.domain.ledger import usd
from atlas.budget.infrastructure.pricing import StaticPricingTable
from atlas.budget.infrastructure.sqlite_repo import SqliteBudgetLedgerRepository
from atlas.config import Settings
from atlas.connectors.domain.tools import TokenUsage
from atlas.connectors.infrastructure.stubs import StubLlmProvider
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.application.service import LightsService
from atlas.lights.infrastructure.stub_controller import StubMatterController
from atlas.persistence.db import Database
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"


def _budget(db: Database) -> BudgetService:
    return BudgetService(
        repo=SqliteBudgetLedgerRepository(db),
        pricing=StaticPricingTable(),
        bus=InProcessEventBus(),
        clock=FrozenClock(datetime(2026, 9, 12, tzinfo=UTC)),
        model="m",
        daily_ceiling=usd("5"),
        timezone="UTC",
        pause_scheduler=lambda: None,
    )


async def test_no_lights_means_no_voice(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    db = Database(tmp_path / "s.db")
    voice = build_voice(
        settings,
        lights=None,
        connectors=build_connectors(settings),
        budget=_budget(db),
        db=db,
        clock=FrozenClock(datetime(2026, 9, 12, tzinfo=UTC)),
    )
    assert voice is None


async def test_dev_wires_a_stub_tier2(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, profile="dev", matter_ws_url="stub", lights_file=FIXTURE)
    db = Database(tmp_path / "s.db")
    clock = FrozenClock(datetime(2026, 9, 12, tzinfo=UTC))
    lights = LightsService(
        registry=LightsRegistry.load(FIXTURE),
        controller=StubMatterController(),
        bus=InProcessEventBus(),
        clock=clock,
    )
    voice = build_voice(
        settings,
        lights=lights,
        connectors=build_connectors(settings),
        budget=_budget(db),
        db=db,
        clock=clock,
    )
    assert voice is not None
    assert voice._tier2 is not None
    provider = voice._tier2._llm
    assert isinstance(provider, StubLlmProvider)
    assert provider._usage == TokenUsage(), "dev must never write fake spend to the ledger"


async def test_prod_wires_anthropic_with_a_fast_fail_budget(tmp_path: Path) -> None:
    """Finding (Tier-2 client): the prod tier-2 provider must fail a
    transient 429/529 fast and explicitly, inside the 6s tier-2 budget,
    rather than let the SDK's own retry loop blow through it."""
    settings = Settings(
        _env_file=None,
        profile="prod",
        anthropic_api_key="test-key",
        matter_ws_url="stub",
        lights_file=FIXTURE,
    )
    db = Database(tmp_path / "s.db")
    clock = FrozenClock(datetime(2026, 9, 12, tzinfo=UTC))
    lights = LightsService(
        registry=LightsRegistry.load(FIXTURE),
        controller=StubMatterController(),
        bus=InProcessEventBus(),
        clock=clock,
    )
    voice = build_voice(
        settings,
        lights=lights,
        connectors=build_connectors(settings),
        budget=_budget(db),
        db=db,
        clock=clock,
    )
    assert voice is not None
    assert voice._tier2 is not None
    client = voice._tier2._llm._client  # type: ignore[attr-defined]
    assert client.timeout == 5.0
    assert client.max_retries == 0
