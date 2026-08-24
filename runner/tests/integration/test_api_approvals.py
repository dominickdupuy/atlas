"""The D16 flow end-to-end through the real ASGI app: request → decide →
frozen payload executed exactly once → events published."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from pihome.approvals.domain.approval import Approval
from pihome.approvals.domain.events import ApprovalDecided
from pihome.bootstrap.container import Application
from pihome.jobs.domain.run import JobRun
from pihome.shared.ids import ApprovalId, new_approval_id, new_run_id
from tests.factories import proposal, tier1_definition
from tests.fakes import EventRecorder
from tests.integration.conftest import AUTH


async def _seed_pending(application: Application) -> ApprovalId:
    """Create a run + pending approval for the fixture job 'lights-out',
    through the real request service (frozen payload persisted)."""
    definition = application.catalog.all_jobs[0]
    assert definition.id == "lights-out"
    run = JobRun.start(
        run_id=new_run_id(),
        definition=definition,
        started_at=datetime.now(UTC),
        scheduled_for=None,
    )
    await application.run_repo.add(run)
    action = proposal(tool="home-assistant.turn_off", summary="Turn off the living room lights")
    return await application.request_approval.request(
        run_id=run.run_id, job_id=run.job_id, action=action, ttl_seconds=3600
    )


async def test_approve_executes_frozen_payload_once(
    application: Application, client: AsyncClient
) -> None:
    recorder = EventRecorder(application.bus)
    approval_id = await _seed_pending(application)

    response = await client.post(
        f"/api/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "applied"
    assert body["state"] == "approved"
    assert body["execution_error"] is None  # stub executed home-assistant.turn_off

    decided = recorder.of_type(ApprovalDecided)
    assert len(decided) == 1

    # Double tap: replay, and no second execution event.
    again = await client.post(
        f"/api/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=AUTH
    )
    assert again.status_code == 200
    assert again.json()["outcome"] == "replay"
    assert len(recorder.of_type(ApprovalDecided)) == 1


async def test_reject_does_not_execute(application: Application, client: AsyncClient) -> None:
    approval_id = await _seed_pending(application)
    response = await client.post(
        f"/api/approvals/{approval_id}/decision", json={"decision": "reject"}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["state"] == "rejected"


async def test_expired_tap_returns_410(application: Application, client: AsyncClient) -> None:
    definition = tier1_definition(id="lights-out", tools=["google-calendar.list_events"])
    run = JobRun.start(
        run_id=new_run_id(),
        definition=definition,
        started_at=datetime.now(UTC),
        scheduled_for=None,
    )
    await application.run_repo.add(run)
    stale = Approval(
        approval_id=new_approval_id(),
        run_id=run.run_id,
        job_id=run.job_id,
        action=proposal(),
        created_at=datetime.now(UTC) - timedelta(days=3),
        expires_at=datetime.now(UTC) - timedelta(days=2),
    )
    await application.approval_repo.add(stale)

    response = await client.post(
        f"/api/approvals/{stale.approval_id}/decision",
        json={"decision": "approve"},
        headers=AUTH,
    )
    assert response.status_code == 410
    assert response.json()["state"] == "expired"


async def test_unknown_approval_is_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/approvals/nonexistent/decision", json={"decision": "approve"}, headers=AUTH
    )
    assert response.status_code == 404


async def test_pending_queue_lists_the_approval(
    application: Application, client: AsyncClient
) -> None:
    approval_id = await _seed_pending(application)
    response = await client.get("/api/approvals", headers=AUTH)
    assert response.status_code == 200
    queue = response.json()
    assert [item["approval_id"] for item in queue] == [approval_id]
    assert queue[0]["summary"] == "Turn off the living room lights"
