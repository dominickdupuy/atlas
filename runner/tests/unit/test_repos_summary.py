"""scripts/repos.py: the one line of a job's log written for the board."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPOS = Path(__file__).parents[3] / "scripts" / "repos.py"
_spec = importlib.util.spec_from_file_location("atlas_repos_script", _REPOS)
assert _spec is not None and _spec.loader is not None
repos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repos)


def test_last_summary_line_wins() -> None:
    lines = [
        "[2026-09-05 08:30:00] run finance (cron): python -m fintrack.autoreview",
        '[2026-09-05 08:30:40] atlas-summary {"transactions": 1200, "uncategorized": 5}',
        "[2026-09-05 08:31:00] applied to the cloud ledger",
        '[2026-09-05 08:31:01] atlas-summary {"transactions": 1204, "uncategorized": 0}',
        "[2026-09-05 08:31:01] exit 0",
    ]

    assert repos.summary_from_log(lines) == {"transactions": 1204, "uncategorized": 0}


def test_broken_or_missing_summary_is_none_not_a_failure() -> None:
    assert repos.summary_from_log(["[t] exit 0"]) is None
    assert repos.summary_from_log(["[t] atlas-summary {not json"]) is None
    assert repos.summary_from_log(["[t] atlas-summary [1, 2]"]) is None
