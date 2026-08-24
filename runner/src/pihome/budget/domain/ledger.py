"""Ledger value objects. Money is integer micro-dollars — never floats."""

from __future__ import annotations

from datetime import datetime
from typing import NewType

from pydantic import BaseModel, ConfigDict

from pihome.connectors.domain.tools import TokenUsage
from pihome.shared.ids import EntryId, JobId, RunId

UsdMicros = NewType("UsdMicros", int)

MICROS_PER_USD = 1_000_000


def usd(amount: str | int) -> UsdMicros:
    """Parse a decimal-string dollar amount into micros exactly (no floats)."""
    text = str(amount)
    sign = -1 if text.startswith("-") else 1
    whole, _, frac = text.lstrip("+-").partition(".")
    frac = (frac + "000000")[:6]
    return UsdMicros(sign * (int(whole or "0") * MICROS_PER_USD + int(frac or "0")))


def format_usd(amount: UsdMicros) -> str:
    return f"${amount / MICROS_PER_USD:.2f}"


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: EntryId
    run_id: RunId | None
    job_id: JobId | None
    model: str
    usage: TokenUsage
    cost_usd_micros: UsdMicros
    recorded_at: datetime
