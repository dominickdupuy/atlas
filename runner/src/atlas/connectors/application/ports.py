"""Connector ports. Adapters (real, stub, and test fakes) live behind these;
nothing outside the connectors context imports an adapter directly."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from atlas.connectors.domain.tools import TokenUsage, ToolCall, ToolResult


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""


class McpClient(Protocol):
    async def list_tools(self) -> list[ToolDescriptor]: ...

    async def call_tool(self, call: ToolCall) -> ToolResult: ...


class LlmRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str
    prompt: str
    max_tokens: int


class LlmResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    usage: TokenUsage
    """From the provider's API usage fields — this is what feeds the budget
    ledger, so an adapter must never fabricate it."""


class LlmProvider(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...


class Forecast(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    temperature_c: float
    high_c: float
    low_c: float
    precipitation_chance_pct: int


class WeatherPort(Protocol):
    async def get_forecast(self, latitude: float, longitude: float) -> Forecast: ...


class CalendarEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    uid: str
    summary: str
    start: datetime
    end: datetime | None = None
    location: str | None = None
    all_day: bool = False
    busy: bool = True


class CalendarFeed(BaseModel):
    """Events plus the health of the fetch that produced them.

    A published calendar feed is a third party that can be slow or down, and
    the board must be able to say "these events are from 40 minutes ago"
    rather than quietly showing yesterday's timetable as though it were live.
    """

    model_config = ConfigDict(frozen=True)

    events: tuple[CalendarEvent, ...] = ()
    fetched_at: datetime | None = None
    error: str | None = None


class CalendarPort(Protocol):
    async def get_events(self, start: datetime, end: datetime) -> CalendarFeed: ...


class NotificationAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    url: str
    method: Literal["POST", "GET"] = "POST"
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    body: str
    priority: Literal["default", "high"] = "default"
    actions: tuple[NotificationAction, ...] = ()


class Notifier(Protocol):
    async def notify(self, notification: Notification) -> None: ...
