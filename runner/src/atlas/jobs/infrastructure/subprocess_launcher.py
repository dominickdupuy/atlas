"""SubprocessJobLauncher: the parent side of the D14 protocol.

Spawn-based (`python -m atlas execute-job`), not fork — identical semantics
on Windows (dev) and Linux (the Pi). One JSON RunRequest goes to the child's
stdin; the child's final stdout line is the RunReport; stderr is the child's
log, relayed line by line with a [job_id/run_id] prefix.

Timeout layering: the child self-limits at max_wall_clock_seconds and exits
cleanly with a timed_out report; the parent enforces a grace margin on top
and kills. Either way the PARENT owns the terminal event (spec §8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from atlas.jobs.application.ports import JobProcessCrash, JobProcessTimeout
from atlas.jobs.domain.run import RunReport, RunRequest

logger = logging.getLogger(__name__)

GRACE_SECONDS = 10.0
_KILL_WAIT_SECONDS = 5.0


class SubprocessJobLauncher:
    async def launch(self, request: RunRequest) -> RunReport:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "atlas",
            "execute-job",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = request.definition.budget.max_wall_clock_seconds + GRACE_SECONDS
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(request.model_dump_json().encode()), timeout=timeout
            )
        except TimeoutError:
            await self._kill(proc)
            raise JobProcessTimeout(
                f"job {request.definition.id} exceeded "
                f"{request.definition.budget.max_wall_clock_seconds}s + grace"
            ) from None

        self._relay_child_logs(request, stderr)

        if proc.returncode != 0:
            raise JobProcessCrash(f"job {request.definition.id} exited with code {proc.returncode}")
        return self._parse_report(request, stdout)

    @staticmethod
    def _relay_child_logs(request: RunRequest, stderr: bytes) -> None:
        prefix = f"[{request.definition.id}/{request.run_id}]"
        for line in stderr.decode(errors="replace").splitlines():
            if line.strip():
                logger.info("%s %s", prefix, line)

    @staticmethod
    def _parse_report(request: RunRequest, stdout: bytes) -> RunReport:
        """NDJSON: progress lines may precede the report; the final
        `{"type": "result", ...}` line is the RunReport."""
        last_result: RunReport | None = None
        for line in stdout.decode(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JobProcessCrash(
                    f"job {request.definition.id} wrote a non-JSON line to stdout: {line[:200]!r}"
                ) from exc
            if isinstance(record, dict) and record.get("type") == "result":
                record.pop("type")
                last_result = RunReport.model_validate(record)
        if last_result is None:
            raise JobProcessCrash(f"job {request.definition.id} produced no result line")
        return last_result

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        proc.terminate()  # on Windows this is already TerminateProcess
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_WAIT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
