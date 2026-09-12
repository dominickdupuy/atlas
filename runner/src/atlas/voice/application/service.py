"""VoiceService (spec 7.2): tier 1, then tier 2 if allowed, then dispatch
through the connectors gateway (D25), compose speech (D29), log everything.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, JsonValue

from atlas.budget.application.service import BudgetService
from atlas.connectors.application.gateway import ToolGateway
from atlas.connectors.domain.tools import ToolCall, ToolResult
from atlas.lights.domain.colour import NAMED_COLOURS, TEMPERATURE_WORDS
from atlas.shared.clock import Clock
from atlas.shared.ids import JobId
from atlas.voice.application.ports import UtteranceLog, UtteranceRecord
from atlas.voice.application.speech import compose
from atlas.voice.application.tier1 import parse
from atlas.voice.application.tier2 import LlmIntentParser
from atlas.voice.domain.intent import Intent, IntentKind, Vocabulary

logger = logging.getLogger(__name__)

VOICE_JOB_ID = JobId("voice")


class VoiceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    speech: str
    intent: Intent | None
    tier: int
    result: dict[str, JsonValue] | None


class VoiceService:
    def __init__(
        self,
        *,
        vocabulary: Vocabulary,
        gateway_factory: Callable[[], ToolGateway],
        log: UtteranceLog,
        clock: Clock,
        tier2: LlmIntentParser | None,
        budget: BudgetService | None,
    ) -> None:
        self._vocabulary = vocabulary
        self._gateway_factory = gateway_factory
        self._log = log
        self._clock = clock
        self._tier2 = tier2
        self._budget = budget

    async def handle(self, text: str) -> VoiceResponse:
        heard_at = self._clock.now()
        first = parse(text, self._vocabulary)
        intent, tier, model = first.intent, 1, None

        if (intent.intent is IntentKind.UNKNOWN or first.partial) and await self._tier2_allowed():
            assert self._tier2 is not None
            # A weak fuzzy scene guess ("bright" ~ "night") is not a
            # trustworthy hint: passing it would bias tier 2 toward a scene
            # it should be reasoning about fresh. A target-only hint still
            # helps, so it still passes.
            weak_scene_guess = first.partial and first.intent.intent is IntentKind.APPLY_SCENE
            hint = (
                first.intent
                if first.intent.intent is not IntentKind.UNKNOWN and not weak_scene_guess
                else None
            )
            outcome = await self._tier2.parse(text, self._vocabulary, hint)
            tier, model = 2, outcome.model
            if outcome.usage is not None and self._budget is not None:
                await self._budget.record_usage(
                    model=outcome.model, usage=outcome.usage, job_id=VOICE_JOB_ID, run_id=None
                )
            if outcome.reason:
                # Never log the utterance text here: it is user speech, and
                # the reason plus tier is enough to see what happened.
                logger.info("tier %d rejected: %s", tier, outcome.reason)
            intent = outcome.intent
        elif first.partial:
            # tier 1 was partial, but tier 2 wasn't run (unconfigured or
            # budget-closed): a partial parse must never actuate on its own.
            intent = Intent(intent=IntentKind.UNKNOWN)

        result = await self._dispatch(intent)
        speech = compose(intent, result)
        try:
            await self._log.add(
                UtteranceRecord(
                    id=uuid.uuid4().hex,
                    heard_at=heard_at,
                    text=text,
                    tier=tier,
                    model=model,
                    intent=intent,
                    outcome=_outcome(intent, result),
                )
            )
        except Exception:
            # A logging failure must never turn a successful actuation into
            # a 500: the bulb already did (or didn't) respond.
            logger.exception("utterance log write failed")
        return VoiceResponse(speech=speech, intent=intent, tier=tier, result=result)

    async def _tier2_allowed(self) -> bool:
        if self._tier2 is None:
            return False
        if self._budget is None:
            return True
        decision = await self._budget.allows()
        if not decision.allowed:
            logger.info("tier-2 skipped: %s", decision.reason)
        return bool(decision.allowed)

    async def _dispatch(self, intent: Intent) -> dict[str, JsonValue] | None:
        gateway = self._gateway_factory()
        match intent.intent:
            case IntentKind.UNKNOWN:
                return None
            case IntentKind.APPLY_SCENE:
                call = ToolCall(tool="lights.scene", args={"name": intent.scene or ""})
            case IntentKind.QUERY:
                call = ToolCall(tool="lights.state")
            case IntentKind.SET_LIGHT:
                call = ToolCall(tool="lights.set", args=_set_args(intent))
        return _content(await gateway.call(call))


def _set_args(intent: Intent) -> dict[str, JsonValue]:
    args: dict[str, JsonValue] = {"targets": list(intent.targets)}
    state = intent.state
    if state is None:
        return args
    if state.power == "toggle":
        args["toggle"] = True
    elif state.power is not None:
        args["on"] = state.power == "on"
    if state.brightness_pct is not None:
        args["brightness"] = state.brightness_pct
    if state.color in TEMPERATURE_WORDS:
        args["color_temp_k"] = TEMPERATURE_WORDS[state.color]
    elif state.color in NAMED_COLOURS:
        hue, saturation = NAMED_COLOURS[state.color]
        args["hue"] = hue
        args["saturation"] = saturation
    return args


def _content(result: ToolResult) -> dict[str, JsonValue]:
    if result.is_error:
        return {"error": str(result.content), "applied": [], "failed": []}
    return dict(result.content) if isinstance(result.content, dict) else {"value": result.content}


def _outcome(intent: Intent, result: dict[str, JsonValue] | None) -> str:
    if intent.intent is IntentKind.UNKNOWN or result is None:
        return "unknown"
    if intent.intent is IntentKind.QUERY:
        return "query"
    if "error" in result:
        return "failed"
    applied = result.get("applied")
    failed = result.get("failed")
    if isinstance(applied, list) and applied and isinstance(failed, list) and failed:
        return "partial"
    if isinstance(applied, list) and applied:
        return "applied"
    return "failed"
