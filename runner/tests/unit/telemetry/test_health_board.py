from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.telemetry.infrastructure.health_board import HealthBoardReader

FIXTURE = Path(__file__).parents[2] / "fixtures" / "health" / "board.json"
NOW = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)


def test_missing_file_is_unavailable_not_an_error(tmp_path: Path) -> None:
    state = HealthBoardReader(tmp_path / "nope.json", "UTC").read(NOW)
    assert state.available is False
    assert state.document is None
    assert "no health board" in state.detail


def test_unparseable_file_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "board.json"
    path.write_text("{not json", encoding="utf-8")
    state = HealthBoardReader(path, "UTC").read(NOW)
    assert state.available is False and state.document is None


def test_a_document_of_the_wrong_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "board.json"
    path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    state = HealthBoardReader(path, "UTC").read(NOW)
    assert state.available is False and "schema" in state.detail


def test_the_fixture_reads_and_is_fresh() -> None:
    state = HealthBoardReader(FIXTURE, "America/New_York").read(
        datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    )
    assert state.available is True
    assert state.stale is False
    assert state.document is not None
    assert state.document["status"]["overall"] == "ALERT"
    assert state.generated_at == datetime.fromisoformat("2026-09-07T10:00:00-04:00")


def test_an_old_document_is_still_served_but_marked_stale() -> None:
    state = HealthBoardReader(FIXTURE, "America/New_York").read(
        datetime(2026, 9, 7, 10, 0, tzinfo=UTC) + timedelta(days=3)
    )
    assert state.available is True and state.stale is True
    assert state.document is not None
