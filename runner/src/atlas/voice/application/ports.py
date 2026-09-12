"""Ports of the voice context."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from atlas.voice.domain.intent import Intent


class UtteranceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    heard_at: datetime
    text: str
    tier: int
    model: str | None
    intent: Intent
    outcome: str


class UtteranceLog(Protocol):
    async def add(self, record: UtteranceRecord) -> None: ...

    async def recent(self, *, limit: int, tier: int | None = None) -> list[UtteranceRecord]: ...
