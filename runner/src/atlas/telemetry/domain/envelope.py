"""The MQTT wire shape: topic + JSON payload + timestamp."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str
    payload: dict[str, JsonValue]
    occurred_at: datetime
