"""The D14 protocol with REAL spawned processes — the reason the launcher is
spawn-based is that these tests pass identically on Windows and Linux.

The stub connectors provide the timeout instrumentation:
PIHOME_STUB_TOOL_DELAY (async — the child's own timeout can fire) and
PIHOME_STUB_TOOL_BLOCK (blocking — only the parent's kill ends it).
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from pihome.jobs.application.ports import JobProcessCrash, JobProcessTimeout
from pihome.jobs.domain.run import RunRequest
from pihome.jobs.infrastructure import subprocess_launcher
from pihome.jobs.infrastructure.subprocess_launcher import SubprocessJobLauncher
from pihome.shared.ids import RunId
from tests.factories import propose_tier1_definition, tier1_definition


@pytest.fixture(autouse=True)
def _dev_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIHOME_PROFILE", "dev")
    monkeypatch.delenv("PIHOME_STUB_TOOL_DELAY", raising=False)
    monkeypatch.delenv("PIHOME_STUB_TOOL_BLOCK", raising=False)


async def test_tier1_round_trip() -> None:
    request = RunRequest(run_id=RunId("it-1"), definition=tier1_definition())
    report = await SubprocessJobLauncher().launch(request)
    assert report.status == "ok"
    assert isinstance(report.output, dict)
    assert "events" in report.output
    assert report.tool_calls == 1


async def test_propose_job_reports_its_proposal() -> None:
    request = RunRequest(run_id=RunId("it-2"), definition=propose_tier1_definition())
    report = await SubprocessJobLauncher().launch(request)
    assert report.status == "ok"
    assert len(report.proposals) == 1
    assert report.proposals[0].call.tool == "home-assistant.turn_off"


async def test_child_self_timeout_reports_timed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIHOME_STUB_TOOL_DELAY", "10")
    definition = tier1_definition(budget={"max_wall_clock_seconds": 1, "max_tool_calls": 3})
    request = RunRequest(run_id=RunId("it-3"), definition=definition)
    report = await SubprocessJobLauncher().launch(request)
    assert report.status == "timed_out"


async def test_parent_kills_a_wedged_child(monkeypatch: pytest.MonkeyPatch) -> None:
    # A blocking sleep freezes the child's event loop, so its own timeout
    # cannot fire; only the parent's grace-period kill ends it.
    monkeypatch.setenv("PIHOME_STUB_TOOL_BLOCK", "30")
    monkeypatch.setattr(subprocess_launcher, "GRACE_SECONDS", 1.0)
    definition = tier1_definition(budget={"max_wall_clock_seconds": 1, "max_tool_calls": 3})
    request = RunRequest(run_id=RunId("it-4"), definition=definition)
    with pytest.raises(JobProcessTimeout):
        await SubprocessJobLauncher().launch(request)


async def test_garbage_stdin_crashes_cleanly() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pihome",
        "execute-job",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, _ = await asyncio.wait_for(proc.communicate(b"this is not a RunRequest"), timeout=30)
    assert proc.returncode != 0


async def test_launcher_surfaces_crash_as_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real misconfiguration: prod profile with no ANTHROPIC_API_KEY makes
    # the child's composition root raise, so the process exits nonzero and
    # the parent must classify it as a crash (spec §8: never silent).
    monkeypatch.setenv("PIHOME_PROFILE", "prod")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    request = RunRequest(run_id=RunId("it-5"), definition=tier1_definition())
    with pytest.raises(JobProcessCrash):
        await SubprocessJobLauncher().launch(request)
