"""The health screen's document, as the board sees it.

`atlas-health` (the separate dominickdupuy/health repo, run as a hosted repo)
writes /var/lib/atlas-health/board.json once a night. The runner does not
compute anything here and never touches Postgres: it reads one file and
passes it through, so the API suite stays hermetic and a broken analysis job
can only make the health screen say so.

Fault-tolerant for the same reason system_metrics.py and hosted_repos.py are:
a missing or half-written file must not take down /api/status.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STALE_AFTER = timedelta(hours=30)
"""The job runs at 10:00, so yesterday's document is normal until this
morning's run should have landed. Beyond that the screen says so rather than
presenting month-old numbers as today's."""


@dataclass(frozen=True)
class HealthBoardState:
    available: bool
    detail: str
    document: dict[str, Any] | None = None
    generated_at: datetime | None = None
    stale: bool = False


class HealthBoardReader:
    def __init__(self, path: Path, tz: str, stale_after: timedelta = STALE_AFTER) -> None:
        self._path = path
        self._tz = ZoneInfo(tz)
        self._stale_after = stale_after

    def _generated_at(self, raw: object) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.replace(tzinfo=self._tz) if parsed.tzinfo is None else parsed

    def read(self, now: datetime) -> HealthBoardState:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is a ValueError, NOT an OSError. A file
            # truncated mid-write can end on a partial multi-byte sequence, and
            # catching OSError alone would let that escape — taking /api/status
            # with it, and blanking every panel on the wall, over a feature
            # nobody happened to be reading.
            return HealthBoardState(False, f"no health board at {self._path}")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("health board: unparseable %s: %s", self._path, exc)
            return HealthBoardState(False, "health board is unreadable")
        if not isinstance(document, dict):
            return HealthBoardState(False, "health board is not an object")
        schema = document.get("schema")
        if schema != SCHEMA_VERSION:
            return HealthBoardState(
                False, f"health board schema {schema!r}, expected {SCHEMA_VERSION}"
            )
        generated_at = self._generated_at(document.get("generated_at"))
        stale = generated_at is None or (now - generated_at) > self._stale_after
        detail = "nightly analysis"
        if stale and generated_at is not None:
            hours = (now - generated_at).total_seconds() / 3600
            detail = f"last computed {hours:.0f} h ago"
        return HealthBoardState(True, detail, document, generated_at, stale)
