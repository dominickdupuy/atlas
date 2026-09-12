"""VoiceService end to end over the stub lights and a scripted LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from atlas.connectors.application.gateway import ToolGateway
from atlas.connectors.domain.tools import TokenUsage, ToolAllowlist
from atlas.connectors.infrastructure.stubs import StubLlmProvider, StubWeather
from atlas.jobs.application.ports import BudgetDecision
from atlas.lights.application.registry import LightsRegistry
from atlas.lights.application.service import LightsService
from atlas.lights.application.tools import LightsTools
from atlas.lights.infrastructure.stub_controller import StubMatterController
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus
from atlas.voice.application.ports import UtteranceRecord
from atlas.voice.application.service import VoiceService
from atlas.voice.application.tier2 import LlmIntentParser
from atlas.voice.domain.intent import IntentKind, Vocabulary

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"
NOW = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)


class _MemoryLog:
    def __init__(self) -> None:
        self.records: list[UtteranceRecord] = []

    async def add(self, record: UtteranceRecord) -> None:
        self.records.append(record)

    async def recent(self, *, limit: int, tier: int | None = None) -> list[UtteranceRecord]:
        return [r for r in self.records if tier is None or r.tier == tier][-limit:]


class _Budget:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.recorded: list[tuple[str, TokenUsage]] = []

    async def allows(self) -> BudgetDecision:
        if self.allowed:
            return BudgetDecision(allowed=True)
        return BudgetDecision(allowed=False, reason="daily spend ceiling reached")

    async def record_usage(
        self, *, model: str, usage: TokenUsage, job_id: object, run_id: object
    ) -> None:
        self.recorded.append((model, usage))


async def _build(
    *,
    llm_replies: tuple[str, ...] = (),
    tier2: bool = True,
    budget: _Budget | None = None,
    confirm_timeout: float = 1.0,
) -> tuple[VoiceService, LightsService, StubMatterController, _MemoryLog, _Budget]:
    stub = StubMatterController()
    registry = LightsRegistry.load(FIXTURE)
    lights = LightsService(
        registry=registry,
        controller=stub,
        bus=InProcessEventBus(),
        clock=FrozenClock(NOW),
        confirm_timeout=confirm_timeout,
    )
    await lights.start()
    tools = LightsTools(lights)
    log = _MemoryLog()
    budget = budget or _Budget()
    parser = (
        LlmIntentParser(
            StubLlmProvider(responses=llm_replies or ('{"intent":"unknown"}',)), model="m-intent"
        )
        if tier2
        else None
    )
    service = VoiceService(
        vocabulary=Vocabulary.from_registry(registry),
        gateway_factory=lambda: ToolGateway(
            allowlist=ToolAllowlist(
                tools=frozenset({"lights.set", "lights.scene", "lights.state"})
            ),
            clients={},
            weather=StubWeather(),
            max_tool_calls=4,
            lights=tools,
        ),
        log=log,
        clock=FrozenClock(NOW),
        tier2=parser,
        budget=budget,  # type: ignore[arg-type]
    )
    return service, lights, stub, log, budget


async def test_tier1_sets_lights_and_logs() -> None:
    service, _, stub, log, budget = await _build()
    response = await service.handle("bedroom to forty percent warm")
    assert response.tier == 1
    assert response.speech == "Bedroom at 40 percent, warm."
    assert response.intent is not None and response.intent.intent is IntentKind.SET_LIGHT
    assert response.result is not None and response.result["failed"] == []
    assert any(s[3] == "moveToColorTemperature" for s in stub.sent)
    assert log.records[0].tier == 1
    assert log.records[0].model is None
    assert log.records[0].outcome == "applied"
    assert budget.recorded == [], "tier 1 spends nothing"


async def test_tier2_runs_only_on_unknown_and_records_usage() -> None:
    reply = '{"intent": "apply_scene", "scene": "night"}'
    service, _, _, log, budget = await _build(llm_replies=(reply,))
    response = await service.handle("bedtime")
    assert response.tier == 2
    assert response.speech == "Night scene."
    assert log.records[0].tier == 2 and log.records[0].model == "m-intent"
    assert budget.recorded[0][0] == "m-intent"


async def test_tier2_gets_the_partial_as_a_hint() -> None:
    reply = '{"intent": "set_light", "targets": ["ceiling-2"], "state": {"power": "on"}}'
    service, _, _, _, _ = await _build(llm_replies=(reply,))
    response = await service.handle("ceiling two")
    assert response.tier == 2
    assert response.speech == "Ceiling 2 on."


async def test_budget_gate_closes_tier2() -> None:
    service, _, _, log, budget = await _build(budget=_Budget(allowed=False))
    response = await service.handle("bedtime")
    assert response.tier == 1
    assert response.speech == "Didn't catch that."
    assert log.records[0].outcome == "unknown"
    assert budget.recorded == []


async def test_no_tier2_configured() -> None:
    service, _, _, _, _ = await _build(tier2=False)
    assert (await service.handle("make it cosy")).speech == "Didn't catch that."


async def test_query_speaks_state() -> None:
    service, _, _, _, _ = await _build()
    await service.handle("ceiling one on")
    response = await service.handle("is ceiling one on")
    assert response.speech == "Ceiling 1 is on."
    assert response.tier == 1


async def test_controller_down_is_spoken() -> None:
    service, lights, _, log, _ = await _build()
    await lights.on_disconnected()
    response = await service.handle("lights off")
    assert response.speech == "The lights controller is offline."
    assert log.records[-1].outcome == "failed"


async def test_partial_failure_names_the_bulb_not_the_controller() -> None:
    service, _, stub, log, _ = await _build(confirm_timeout=0.05)
    # Turn the bedroom on first: the bulbs start off, and "off" on an
    # already-off bulb confirms trivially with no wait, which would hide the
    # silent node's failure to respond.
    await service.handle("bedroom on")
    stub.silent_nodes = {3}
    response = await service.handle("bedroom off")
    assert response.speech == "Bedroom off, ceiling 3 didn't respond."
    assert log.records[-1].outcome == "partial"
    assert response.result is not None and response.result["failed"] == ["ceiling-3"]
