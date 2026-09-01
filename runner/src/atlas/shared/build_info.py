"""What revision is actually running.

The deploy loop's whole purpose is answering "did the new code land?", and
the honest answer has to come from the running process, not from the branch
someone believes they pushed. Resolved once at startup — a deploy restarts
the service, so a cached value can never be stale.
"""

from __future__ import annotations

import logging
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"


def package_version(name: str = "atlas") -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return UNKNOWN


def git_revision(repo: Path | None = None, timeout: float = 2.0) -> str:
    """Short SHA of the checkout, or 'unknown' off a git tree.

    Shelling out rather than parsing .git by hand: packed refs, worktrees and
    detached HEADs are all cases git already gets right, and this runs once.
    """
    cwd = repo or Path(__file__).resolve().parents[3]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git revision unavailable: %s", exc)
        return UNKNOWN
    if completed.returncode != 0:
        return UNKNOWN
    return completed.stdout.strip() or UNKNOWN
