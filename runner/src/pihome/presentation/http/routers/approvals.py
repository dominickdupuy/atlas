"""The D16 approval interface, verbatim:

    POST /api/approvals/{id}/decision   {"decision": "approve" | "reject"}
    GET  /api/approvals                 pending queue

Decision responses distinguish applied vs idempotent replay (both 200) vs
expired (410) vs unknown (404).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pihome.approvals.domain.approval import Decision
from pihome.bootstrap.container import Application
from pihome.presentation.http.routers._deps import get_application
from pihome.shared.ids import ApprovalId

router = APIRouter(prefix="/api/approvals")


class DecisionBody(BaseModel):
    decision: Literal["approve", "reject"]


@router.post("/{approval_id}/decision")
async def decide(
    approval_id: str,
    body: DecisionBody,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    result = await application.decide_approval.decide(
        ApprovalId(approval_id), Decision(body.decision), source="api"
    )
    if result.outcome == "not_found":
        return JSONResponse({"detail": "unknown approval"}, status_code=404)

    payload = {
        "approval_id": approval_id,
        "outcome": result.outcome,
        "state": str(result.state) if result.state else None,
        "execution_error": result.execution_error,
    }
    status_code = 410 if result.outcome == "expired" else 200
    return JSONResponse(payload, status_code=status_code)


@router.get("")
async def pending(
    application: Annotated[Application, Depends(get_application)],
) -> list[dict[str, str]]:
    return [
        {
            "approval_id": approval.approval_id,
            "job_id": approval.job_id,
            "run_id": approval.run_id,
            "summary": approval.action.summary,
            "created_at": approval.created_at.isoformat(),
            "expires_at": approval.expires_at.isoformat(),
        }
        for approval in await application.approval_repo.pending()
    ]
