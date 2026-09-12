"""Tier 2 with a scripted LLM: only a valid, in-vocabulary JSON reply becomes
an intent; everything else is unknown and spends nothing further."""

from __future__ import annotations

import asyncio
from pathlib import Path

from atlas.connectors.application.ports import LlmRequest, LlmResponse
from atlas.connectors.domain.tools import TokenUsage
from atlas.connectors.infrastructure.stubs import StubLlmProvider
from atlas.lights.application.registry import LightsRegistry
from atlas.voice.application.tier2 import LlmIntentParser
from atlas.voice.domain.intent import Intent, IntentKind, StateWords, Vocabulary

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "lights.yaml"
VOCAB = Vocabulary.from_registry(LightsRegistry.load(FIXTURE))
USAGE = TokenUsage(input_tokens=300, output_tokens=20)


def _parser(*replies: str) -> tuple[LlmIntentParser, StubLlmProvider]:
    llm = StubLlmProvider(responses=replies, usage=USAGE)
    return LlmIntentParser(llm, model="claude-haiku-4-5-20251001"), llm


async def test_valid_json_becomes_an_intent() -> None:
    parser, llm = _parser(
        '{"intent": "set_light", "targets": ["bedroom"], '
        '"state": {"brightness_pct": 20, "color": "warm"}}'
    )
    outcome = await parser.parse("make it cosy in here", VOCAB, hint=None)
    assert outcome.intent == Intent(
        intent=IntentKind.SET_LIGHT,
        targets=("bedroom",),
        state=StateWords(brightness_pct=20, color="warm"),
    )
    assert outcome.usage == USAGE
    assert outcome.model == "claude-haiku-4-5-20251001"
    assert outcome.reason is None
    request = llm.requests[0]
    assert "make it cosy in here" in request.prompt
    assert "ceiling-1" in request.system and "evening" in request.system
    assert "exactly one JSON object" in request.system


async def test_fenced_json_is_accepted() -> None:
    parser, _ = _parser('```json\n{"intent": "apply_scene", "scene": "night"}\n```')
    outcome = await parser.parse("bedtime", VOCAB, hint=None)
    assert outcome.intent.scene == "night"


async def test_invalid_replies_are_unknown_with_usage_still_counted() -> None:
    for reply in (
        "Sure! Turning the lights warm.",
        '{"intent": "set_light", "targets": ["kitchen"], "state": {"power": "on"}}',
        '{"intent": "set_light", "targets": ["all"], "state": {"power": "on"}, "note": "hi"}',
        '{"intent": "set_light", "targets": ["all"], "state": {"color": "plaid"}}',
        '{"intent": "apply_scene", "scene": "party"}',
    ):
        parser, _ = _parser(reply)
        outcome = await parser.parse("x", VOCAB, hint=None)
        assert outcome.intent.intent is IntentKind.UNKNOWN, reply
        assert outcome.reason
        assert outcome.usage == USAGE


async def test_provider_failure_and_timeout_are_unknown_with_no_usage() -> None:
    class Boom:
        async def complete(self, request: LlmRequest) -> LlmResponse:
            raise RuntimeError("api down")

    class Slow:
        async def complete(self, request: LlmRequest) -> LlmResponse:
            await asyncio.sleep(1)
            return LlmResponse(text="{}", usage=USAGE)

    boom = await LlmIntentParser(Boom(), model="m").parse("x", VOCAB, hint=None)
    assert boom.intent.intent is IntentKind.UNKNOWN and boom.usage is None
    slow = await LlmIntentParser(Slow(), model="m", timeout=0.01).parse("x", VOCAB, hint=None)
    assert slow.intent.intent is IntentKind.UNKNOWN and "timeout" in (slow.reason or "")


async def test_hint_is_passed_as_data() -> None:
    parser, llm = _parser('{"intent": "unknown"}')
    hint = Intent(intent=IntentKind.SET_LIGHT, targets=("ceiling-2",))
    await parser.parse("ceiling two", VOCAB, hint=hint)
    assert '"targets": ["ceiling-2"]' in llm.requests[0].prompt
