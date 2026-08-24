"""ExecuteJobService: the parent-side orchestration of one job run.

Owns the §6.2 flow: publish started → pre-flight budget → spawn the child →
record usage → apply the D8 mode gate → persist → publish a terminal event.
The terminal-event guarantee (spec §8) lives here structurally: every path
out of `execute` ends in exactly one of completed / failed /
awaiting_approval.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pihome.jobs.application.ports import (
    ApprovalRequester,
    BudgetGate,
    JobProcessCrash,
    JobProcessLauncher,
    JobProcessTimeout,
    JobRunRepository,
    WriteExecutor,
)
from pihome.jobs.domain import policies
from pihome.jobs.domain.definition import JobDefinition, Tier
from pihome.jobs.domain.events import (
    JobRunAwaitingApproval,
    JobRunCompleted,
    JobRunFailed,
    JobRunStarted,
)
from pihome.jobs.domain.run import JobRun, RunReport, RunRequest
from pihome.shared.clock import Clock
from pihome.shared.events import InProcessEventBus
from pihome.shared.ids import new_run_id

logger = logging.getLogger(__name__)


class ExecuteJobService:
    def __init__(
        self,
        *,
        repo: JobRunRepository,
        launcher: JobProcessLauncher,
        budget: BudgetGate,
        approvals: ApprovalRequester,
        writes: WriteExecutor,
        bus: InProcessEventBus,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._launcher = launcher
        self._budget = budget
        self._approvals = approvals
        self._writes = writes
        self._bus = bus
        self._clock = clock

    async def execute(
        self, definition: JobDefinition, scheduled_for: datetime | None, attempt: int = 1
    ) -> JobRun:
        run = JobRun.start(
            run_id=new_run_id(),
            definition=definition,
            started_at=self._clock.now(),
            scheduled_for=scheduled_for,
            attempt=attempt,
        )
        await self._repo.add(run)
        await self._bus.publish(
            JobRunStarted(
                occurred_at=run.started_at,
                job_id=run.job_id,
                run_id=run.run_id,
                tier=run.tier,
                mode=run.mode,
                attempt=attempt,
            )
        )

        if definition.tier is not Tier.DETERMINISTIC:
            decision = await self._budget.preflight(definition)
            if not decision.allowed:
                return await self._fail(run, definition, f"budget pre-flight: {decision.reason}")

        try:
            report = await self._launcher.launch(
                RunRequest(run_id=run.run_id, attempt=attempt, definition=definition)
            )
        except JobProcessTimeout:
            timed_out = run.timed_out(self._clock.now())
            await self._repo.update(timed_out)
            await self._publish_failed(timed_out, definition, timed_out.error or "timed out")
            return timed_out
        except JobProcessCrash as crash:
            return await self._fail(run, definition, f"job process crashed: {crash}")

        if report.usage.total > 0:
            await self._budget.record(definition, run.run_id, report.usage)

        if report.status == "timed_out":
            timed_out = run.timed_out(self._clock.now())
            await self._repo.update(timed_out)
            await self._publish_failed(timed_out, definition, timed_out.error or "timed out")
            return timed_out
        if report.status == "error":
            failed = await self._fail(run, definition, report.error or "job reported an error")
            if policies.should_retry(definition, attempt):
                logger.info("retrying %s (attempt %d)", definition.id, attempt + 1)
                return await self.execute(definition, scheduled_for, attempt=attempt + 1)
            return failed

        return await self._route(run, definition, report)

    async def _route(self, run: JobRun, definition: JobDefinition, report: RunReport) -> JobRun:
        outcome = policies.route(definition, report)
        now = self._clock.now()

        match outcome:
            case policies.PublishResult(dropped_proposals=dropped):
                if dropped:
                    logger.warning(
                        "read-mode job %s produced %d proposal(s); dropped (D8)",
                        definition.id,
                        len(dropped),
                    )
                completed = run.completed(report, now)
                await self._repo.update(completed)
                await self._bus.publish(
                    JobRunCompleted(
                        occurred_at=now,
                        job_id=run.job_id,
                        run_id=run.run_id,
                        output=report.output,
                        publish_to=definition.output.publish_to,
                    )
                )
                return completed

            case policies.ProposeApproval(proposals=proposals):
                assert definition.approval_ttl_seconds is not None  # schema-guaranteed
                approval_ids = [
                    await self._approvals.request(
                        run_id=run.run_id,
                        job_id=run.job_id,
                        action=action,
                        ttl_seconds=definition.approval_ttl_seconds,
                    )
                    for action in proposals
                ]
                waiting = run.awaiting_approval(report, now)
                await self._repo.update(waiting)
                await self._bus.publish(
                    JobRunAwaitingApproval(
                        occurred_at=now,
                        job_id=run.job_id,
                        run_id=run.run_id,
                        approval_ids=tuple(approval_ids),
                        summaries=tuple(action.summary for action in proposals),
                    )
                )
                return waiting

            case policies.ExecuteWrite(actions=actions):
                for action in actions:
                    result = await self._writes.execute(definition, action)
                    if result.is_error:
                        return await self._fail(
                            run, definition, f"write action failed: {result.content}"
                        )
                completed = run.completed(report, self._clock.now())
                await self._repo.update(completed)
                await self._bus.publish(
                    JobRunCompleted(
                        occurred_at=completed.finished_at or now,
                        job_id=run.job_id,
                        run_id=run.run_id,
                        output=report.output,
                        publish_to=definition.output.publish_to,
                    )
                )
                return completed

    async def _fail(self, run: JobRun, definition: JobDefinition, error: str) -> JobRun:
        failed = run.failed(error, self._clock.now())
        await self._repo.update(failed)
        await self._publish_failed(failed, definition, error)
        return failed

    async def _publish_failed(self, run: JobRun, definition: JobDefinition, error: str) -> None:
        consecutive = await self._repo.consecutive_failures(run.job_id)
        await self._bus.publish(
            JobRunFailed(
                occurred_at=run.finished_at or self._clock.now(),
                job_id=run.job_id,
                run_id=run.run_id,
                error=error,
                consecutive_failures=consecutive,
                escalate=consecutive >= definition.on_failure.escalate_after,
            )
        )
