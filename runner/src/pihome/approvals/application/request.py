"""RequestApprovalService: freeze the payload, persist it, notify the phone.

Implements the jobs context's ApprovalRequester port. The notification's
action buttons POST straight to the D16 decision endpoint — a phone
notification cannot publish to a broker, which is why the return path is
HTTP in the first place (D16).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from pihome.approvals.application.ports import ApprovalRepository
from pihome.approvals.domain.approval import Approval
from pihome.approvals.domain.events import ApprovalRequested
from pihome.connectors.application.ports import Notification, NotificationAction, Notifier
from pihome.jobs.domain.run import ProposedAction
from pihome.shared.clock import Clock
from pihome.shared.events import InProcessEventBus
from pihome.shared.ids import ApprovalId, JobId, RunId, new_approval_id

logger = logging.getLogger(__name__)


class RequestApprovalService:
    def __init__(
        self,
        *,
        repo: ApprovalRepository,
        notifier: Notifier,
        bus: InProcessEventBus,
        clock: Clock,
        public_url: str,
        api_token: str,
    ) -> None:
        self._repo = repo
        self._notifier = notifier
        self._bus = bus
        self._clock = clock
        self._public_url = public_url.rstrip("/")
        self._api_token = api_token

    async def request(
        self, *, run_id: RunId, job_id: JobId, action: ProposedAction, ttl_seconds: int
    ) -> ApprovalId:
        now = self._clock.now()
        approval = Approval(
            approval_id=new_approval_id(),
            run_id=run_id,
            job_id=job_id,
            action=action,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        await self._repo.add(approval)
        await self._bus.publish(
            ApprovalRequested(
                occurred_at=now,
                approval_id=approval.approval_id,
                run_id=run_id,
                job_id=job_id,
                summary=action.summary,
                expires_at_iso=approval.expires_at.isoformat(),
            )
        )
        await self._notify(approval)
        return approval.approval_id

    async def _notify(self, approval: Approval) -> None:
        decision_url = f"{self._public_url}/api/approvals/{approval.approval_id}/decision"
        # The API bearer token rides inside the ntfy action definition. That
        # is acceptable within an authenticated ntfy topic on the tailnet
        # (D12); per-approval signed tokens are the upgrade path if this
        # ever leaves the tailnet.
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }
        notification = Notification(
            title=f"Approval: {approval.job_id}",
            body=approval.action.summary,
            priority="high",
            actions=(
                NotificationAction(
                    label="Approve",
                    url=decision_url,
                    body=json.dumps({"decision": "approve"}),
                    headers=headers,
                ),
                NotificationAction(
                    label="Reject",
                    url=decision_url,
                    body=json.dumps({"decision": "reject"}),
                    headers=headers,
                ),
            ),
        )
        try:
            await self._notifier.notify(notification)
        except Exception:
            # The approval still exists and shows on the board; a failed
            # push must not fail the job run.
            logger.exception("approval notification failed for %s", approval.approval_id)
