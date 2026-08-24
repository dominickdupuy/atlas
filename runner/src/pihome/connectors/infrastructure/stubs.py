"""Dev-profile stand-ins (PIHOME_PROFILE=dev): the whole stack runs on a
machine with zero credentials — including the machine this repo was
scaffolded on, before the Pi existed.

These are also the fakes the tier-executor unit tests script, so dev
behavior and tested behavior are the same code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Sequence

from pydantic import JsonValue

from pihome.connectors.application.ports import (
    Forecast,
    LlmRequest,
    LlmResponse,
    Notification,
    ToolDescriptor,
)
from pihome.connectors.domain.tools import TokenUsage, ToolCall, ToolResult

logger = logging.getLogger(__name__)

_CANNED: dict[str, JsonValue] = {
    "google-calendar.list_events": [
        {"start": "09:00", "end": "09:30", "title": "Standup"},
        {"start": "12:00", "end": "13:00", "title": "Lunch with Sam"},
        {"start": "15:00", "end": "16:00", "title": "Architecture review"},
    ],
    "github.list_notifications": [
        {"repo": "pi-home", "subject": "CI passed on main", "reason": "ci_activity"},
    ],
    "home-assistant.get_state": {"entity_id": "light.living_room", "state": "off"},
    "home-assistant.turn_on": {"ok": True},
    "home-assistant.turn_off": {"ok": True},
}


class StubMcpClient:
    """Returns canned payloads by tool name; unknown tools error the way a
    real server would."""

    def __init__(self, canned: dict[str, JsonValue] | None = None) -> None:
        self._canned = canned if canned is not None else _CANNED

    async def list_tools(self) -> list[ToolDescriptor]:
        return [ToolDescriptor(name=name.partition(".")[2]) for name in self._canned]

    async def call_tool(self, call: ToolCall) -> ToolResult:
        # Test instrumentation for the D14 timeout paths (see
        # tests/integration/test_subprocess_exec.py): an async delay lets the
        # child's own timeout fire; a BLOCKING sleep freezes the child's event
        # loop so only the parent's kill can end it.
        delay = float(os.environ.get("PIHOME_STUB_TOOL_DELAY", "0"))
        if delay > 0:
            await asyncio.sleep(delay)
        block = float(os.environ.get("PIHOME_STUB_TOOL_BLOCK", "0"))
        if block > 0:
            time.sleep(block)  # noqa: ASYNC251 - deliberate: simulates a wedged child
        if call.tool not in self._canned:
            return ToolResult(
                tool=call.tool, content=f"stub has no canned data for {call.tool}", is_error=True
            )
        return ToolResult(tool=call.tool, content=self._canned[call.tool])


class StubLlmProvider:
    """Deterministic text and scripted usage numbers, so budget math is
    testable to the token."""

    def __init__(
        self,
        responses: Sequence[str] = ("This is a stub model response.",),
        usage: TokenUsage | None = None,
    ) -> None:
        self._responses = list(responses)
        default_usage = TokenUsage(input_tokens=350, output_tokens=120)
        self._usage = usage if usage is not None else default_usage
        self._call_index = 0
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        text = self._responses[min(self._call_index, len(self._responses) - 1)]
        self._call_index += 1
        return LlmResponse(text=text, usage=self._usage)


class StubWeather:
    async def get_forecast(self, latitude: float, longitude: float) -> Forecast:
        return Forecast(
            summary="partly cloudy",
            temperature_c=21.0,
            high_c=24.0,
            low_c=15.0,
            precipitation_chance_pct=20,
        )


class LogNotifier:
    """Dev fallback when no ntfy token is configured: the notification is a
    log line instead of a push."""

    async def notify(self, notification: Notification) -> None:
        logger.info(
            "NOTIFICATION [%s] %s — %s (%d action(s))",
            notification.priority,
            notification.title,
            notification.body,
            len(notification.actions),
        )
