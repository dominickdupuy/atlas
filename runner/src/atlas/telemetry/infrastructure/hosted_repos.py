"""Hosted repositories (docs/repos.md) as the Runs panel sees them.

`scripts/repos.py` keeps ordinary checkouts — not atlas jobs — cloned,
scheduled and logged. None of that reaches the runner, so until now the
board could show an idle screen while a hosted job was mid-run, and show
nothing at all about work due in the next hour.

Read from the live sources, never /var/lib/atlas-repos/status.json: that
file is written only when someone runs `repos.py status` by hand — no cron
line renders it — so a board trusting it would present a snapshot of unknown
age as current, which is exactly the lie D11 forbids. The registry, the
per-run summaries and the queue are each rewritten at the moment they
change.

Every reader is individually fault-tolerant and returns empty rather than
raising, for the reason system_metrics.py is: a missing repos.toml must not
take down the panel whose job is to show failures.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

logger = logging.getLogger(__name__)

_QUEUE_FILE = "queue.json"
_SCHEDULED_KINDS = ("job", "both")


def _figures(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


@dataclass(frozen=True)
class HostedRepoRun:
    """The last run of one repo, from /var/lib/atlas-repos/<name>.json."""

    name: str
    status: str
    trigger: str
    started: datetime | None
    duration_seconds: float | None
    exit_code: int | None
    summary: dict[str, int] = field(default_factory=dict)
    """Figures the job printed for the board (repos.py's `atlas-summary` line):
    integer-valued only, so a job cannot push prose onto the screen."""

    @property
    def failed(self) -> bool:
        # repos.py writes "running", "ok", or "failed"; treat anything unknown as
        # failed rather than quietly drawing it green.
        return self.status not in ("ok", "running")


@dataclass(frozen=True)
class HostedRepoFire:
    """Work a repo will do: its next cron fire, or a queued one-off."""

    name: str
    at: datetime
    source: str
    note: str = ""


class HostedRepoReader:
    def __init__(self, registry: Path, state_dir: Path, tz: str) -> None:
        self._registry = registry
        self._state_dir = state_dir
        self._tz = ZoneInfo(tz)

    def _repos(self) -> list[dict[str, object]]:
        try:
            parsed = tomllib.loads(self._registry.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.debug("hosted repos: cannot read %s: %s", self._registry, exc)
            return []
        entries = parsed.get("repo", [])
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("name")]

    def _aware(self, raw: object) -> datetime | None:
        """repos.py writes naive host-local timestamps; the board compares
        them against tz-aware run times, so stamp them here."""
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.replace(tzinfo=self._tz) if parsed.tzinfo is None else parsed

    def last_runs(self) -> list[HostedRepoRun]:
        runs: list[HostedRepoRun] = []
        for repo in self._repos():
            name = str(repo["name"])
            try:
                raw = (self._state_dir / f"{name}.json").read_text(encoding="utf-8")
                state = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue  # never run yet, or unreadable: not an error
            if not isinstance(state, dict):
                continue
            exit_code = state.get("exit")
            duration = state.get("duration_seconds")
            runs.append(
                HostedRepoRun(
                    name=name,
                    status=str(state.get("status", "unknown")),
                    trigger=str(state.get("trigger", "")),
                    started=self._aware(state.get("started")),
                    duration_seconds=float(duration) if isinstance(duration, int | float) else None,
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    summary=_figures(state.get("summary")),
                )
            )
        return runs

    def _queued(self) -> list[HostedRepoFire]:
        try:
            raw = (self._state_dir / _QUEUE_FILE).read_text(encoding="utf-8")
            entries = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(entries, list):
            return []
        fires: list[HostedRepoFire] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = self._aware(entry.get("at"))
            name = entry.get("name")
            if at is None or not isinstance(name, str):
                continue
            fires.append(
                HostedRepoFire(name=name, at=at, source="queued", note=str(entry.get("note") or ""))
            )
        return fires

    def _next_cron_fires(self, now: datetime) -> list[HostedRepoFire]:
        fires: list[HostedRepoFire] = []
        for repo in self._repos():
            if repo.get("enabled") is False:
                continue
            if repo.get("kind") not in _SCHEDULED_KINDS:
                continue
            schedule = repo.get("schedule")
            if not isinstance(schedule, str) or not schedule:
                continue
            try:
                local = now.astimezone(self._tz)
                fire: datetime = croniter(schedule, local).get_next(datetime)
            except (ValueError, KeyError) as exc:
                logger.debug("hosted repos: bad schedule for %s: %s", repo["name"], exc)
                continue
            fires.append(
                HostedRepoFire(
                    name=str(repo["name"]), at=fire.astimezone(now.tzinfo), source="cron"
                )
            )
        return fires

    def upcoming(self, now: datetime) -> list[HostedRepoFire]:
        """Queued one-offs and next cron fires, soonest first.

        A queued one-off is real work already committed to; a cron fire is a
        prediction. Both belong on the panel, and `source` keeps them
        distinguishable.
        """
        merged = self._queued() + self._next_cron_fires(now)
        return sorted(merged, key=lambda fire: fire.at)
