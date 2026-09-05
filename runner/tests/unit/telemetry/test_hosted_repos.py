"""HostedRepoReader: the Runs panel's view of scripts/repos.py.

Everything here is files on disk, so the tests write a tmp registry and
state directory rather than needing the Pi's /var/lib.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from atlas.telemetry.infrastructure.hosted_repos import HostedRepoReader

REGISTRY = """
[[repo]]
name = "finance"
path = "/opt/finance"
url = "git@example:acme/finance.git"
kind = "job"
schedule = "30 8 * * *"
job = "run-it"

[[repo]]
name = "dashboardsvc"
path = "/opt/svc"
url = "git@example:acme/svc.git"
kind = "service"
service = "serve-it"

[[repo]]
name = "retired"
path = "/opt/old"
url = "git@example:acme/old.git"
kind = "job"
schedule = "0 * * * *"
job = "nope"
enabled = false
"""


def _reader(tmp_path: Path, registry: str = REGISTRY) -> HostedRepoReader:
    registry_file = tmp_path / "repos.toml"
    registry_file.write_text(registry, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return HostedRepoReader(registry_file, state, "UTC")


def _write_state(tmp_path: Path, name: str, **fields: object) -> None:
    payload = {
        "name": name,
        "kind": "job",
        "trigger": "cron",
        "started": "2026-09-05T16:52:20",
        "status": "ok",
        "duration_seconds": 57.2,
        "exit": 0,
    }
    payload.update(fields)
    (tmp_path / "state" / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_last_runs_reads_the_per_run_summary(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    _write_state(tmp_path, "finance")

    (run,) = reader.last_runs()

    assert run.name == "finance"
    assert run.failed is False
    assert run.duration_seconds == 57.2
    assert run.started == datetime(2026, 9, 5, 16, 52, 20, tzinfo=UTC)


def test_a_repo_that_has_never_run_is_simply_absent(tmp_path: Path) -> None:
    assert _reader(tmp_path).last_runs() == []


def test_unknown_status_counts_as_failed(tmp_path: Path) -> None:
    """Better a false alarm than a green row for a run nobody can classify."""
    reader = _reader(tmp_path)
    _write_state(tmp_path, "finance", status="weird")

    assert reader.last_runs()[0].failed is True


def test_upcoming_includes_the_next_cron_fire(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    fires = _reader(tmp_path).upcoming(now)

    assert [(f.name, f.source) for f in fires] == [("finance", "cron")]
    assert fires[0].at == datetime(2026, 9, 6, 8, 30, tzinfo=UTC)


def test_disabled_and_service_repos_never_appear_as_scheduled(tmp_path: Path) -> None:
    fires = _reader(tmp_path).upcoming(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))

    names = {fire.name for fire in fires}
    assert "retired" not in names  # enabled = false
    assert "dashboardsvc" not in names  # a service has no schedule


def test_queued_one_offs_sort_ahead_of_a_later_cron_fire(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    (tmp_path / "state" / "queue.json").write_text(
        json.dumps(
            [
                {
                    "id": "finance-1",
                    "name": "finance",
                    "at": "2026-09-05T13:00",
                    "note": "after the key fix",
                }
            ]
        ),
        encoding="utf-8",
    )

    fires = reader.upcoming(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))

    first = fires[0]
    assert (first.source, first.note) == ("queued", "after the key fix")
    assert [f.source for f in fires] == ["queued", "cron"]


def test_a_missing_registry_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    reader = HostedRepoReader(tmp_path / "absent.toml", tmp_path, "UTC")

    assert reader.last_runs() == []
    assert reader.upcoming(datetime(2026, 9, 5, 12, 0, tzinfo=UTC)) == []


def test_unparseable_registry_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    reader = _reader(tmp_path, registry="this is not toml [[[")

    assert reader.upcoming(datetime(2026, 9, 5, 12, 0, tzinfo=UTC)) == []


def test_corrupt_state_file_is_skipped(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    (tmp_path / "state" / "finance.json").write_text("{ truncated", encoding="utf-8")

    assert reader.last_runs() == []
