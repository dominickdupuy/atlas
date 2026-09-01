"""GET /api/status — everything the board draws, in one document.

One request per refresh is deliberate: the display polls this on a timer and
must never paint a half-updated screen, and a single call gives the browser
one unambiguous success/failure signal to base its stale-data warning on.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from atlas.presentation.http.status import StatusAssembler, StatusSnapshot

router = APIRouter(prefix="/api")


def get_status_assembler(request: Request) -> StatusAssembler:
    assembler: StatusAssembler = request.app.state.status
    return assembler


@router.get("/status")
async def status(
    assembler: Annotated[StatusAssembler, Depends(get_status_assembler)],
) -> StatusSnapshot:
    return await assembler.snapshot()
