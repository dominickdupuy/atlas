"""The child side of the D14 protocol: what `atlas execute-job` runs.

Reads one RunRequest from stdin, executes the tier under its own wall-clock
limit, and writes the RunReport as the final NDJSON line on stdout. All
logging goes to stderr. The child never touches MQTT or SQLite — it reports;
the parent decides and records.

Debugging a job is running this by hand:
    uv run atlas execute-job < request.json
"""

from __future__ import annotations

import asyncio
import json
import sys

from atlas.jobs.domain.run import RunReport, RunRequest


async def run_child(request: RunRequest) -> RunReport:
    # Imported here so the child builds only what it needs (connectors), and
    # so a parent-side import problem can never take the protocol down with it.
    from atlas.bootstrap.child import build_tier_executor

    executor = build_tier_executor(request.definition)
    try:
        async with asyncio.timeout(request.definition.budget.max_wall_clock_seconds):
            return await executor.execute(request.definition)
    except TimeoutError:
        return RunReport(status="timed_out", error="wall clock budget exceeded in child")


def child_entrypoint() -> int:
    raw = sys.stdin.read()
    request = RunRequest.model_validate_json(raw)
    report = asyncio.run(run_child(request))
    record = {"type": "result", **json.loads(report.model_dump_json())}
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
    return 0
