"""Shared fixtures. Network is blocked suite-wide except loopback (see
pyproject addopts); no fixture here may open a socket."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.persistence.db import Database
from atlas.shared.clock import FrozenClock
from atlas.shared.events import InProcessEventBus

# A Monday noon UTC; cron tests depend on the weekday being known.
START = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(START)


@pytest.fixture
def bus() -> InProcessEventBus:
    return InProcessEventBus()


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "state.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()
