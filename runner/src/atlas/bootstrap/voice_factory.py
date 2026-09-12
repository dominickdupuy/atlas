"""Voice wiring (D22, D24-D29). Exists only when lights do: the grammar is
built from the lights registry, and there is nothing else to say yet."""

from __future__ import annotations

import logging

from atlas.bootstrap.connectors_factory import Connectors
from atlas.budget.application.service import BudgetService
from atlas.config import Settings
from atlas.connectors.application.gateway import ToolGateway
from atlas.connectors.domain.tools import TokenUsage, ToolAllowlist
from atlas.connectors.infrastructure.stubs import StubLlmProvider
from atlas.lights.application.service import LightsService
from atlas.lights.application.tools import LightsTools
from atlas.persistence.db import Database
from atlas.shared.clock import Clock
from atlas.voice.application.service import VoiceService
from atlas.voice.application.tier2 import LlmIntentParser
from atlas.voice.domain.intent import Vocabulary
from atlas.voice.infrastructure.sqlite_log import SqliteUtteranceLog

logger = logging.getLogger(__name__)

VOICE_TOOLS = frozenset({"lights.set", "lights.scene", "lights.state"})
MAX_TOOL_CALLS_PER_UTTERANCE = 4


def build_voice(
    settings: Settings,
    *,
    lights: LightsService | None,
    connectors: Connectors,
    budget: BudgetService,
    db: Database,
    clock: Clock,
) -> VoiceService | None:
    if lights is None:
        return None
    tools = LightsTools(lights)
    vocabulary = Vocabulary.from_registry(lights.registry)

    tier2: LlmIntentParser | None
    if settings.profile == "dev":
        # Dev must spend nothing: a real TokenUsage would write fake spend
        # to the budget ledger every time the stub answers.
        tier2 = LlmIntentParser(
            StubLlmProvider(responses=('{"intent": "unknown"}',), usage=TokenUsage()),
            model="stub",
        )
    elif settings.anthropic_api_key:
        from atlas.connectors.infrastructure.anthropic_llm import AnthropicLlmProvider

        tier2 = LlmIntentParser(
            AnthropicLlmProvider(
                settings.anthropic_api_key,
                settings.intent_model,
                # The tier-2 budget is 6s total (LlmIntentParser's own
                # timeout); failing a transient 429/529 fast and without a
                # client-side retry keeps a retry loop from blowing that
                # budget on its own.
                timeout=5.0,
                max_retries=0,
            ),
            model=settings.intent_model,
        )
    else:
        logger.warning("voice: no ANTHROPIC_API_KEY; tier 2 disabled, tier 1 only")
        tier2 = None

    def gateway() -> ToolGateway:
        return ToolGateway(
            allowlist=ToolAllowlist(tools=VOICE_TOOLS),
            clients=connectors.clients,
            weather=connectors.weather,
            max_tool_calls=MAX_TOOL_CALLS_PER_UTTERANCE,
            lights=tools,
        )

    return VoiceService(
        vocabulary=vocabulary,
        gateway_factory=gateway,
        log=SqliteUtteranceLog(db),
        clock=clock,
        tier2=tier2,
        budget=budget,
    )
