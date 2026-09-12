"""POST /api/voice (D22, D24, D29): one utterance in, speech plus structure out.

The echo branch is the day-one contract the Shortcut is built against and
the fallback when the voice context is not wired (no lights configured).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from atlas.bootstrap.container import Application
from atlas.presentation.http.routers._deps import get_application

router = APIRouter(prefix="/api/voice")


class VoiceBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("")
async def voice(
    body: VoiceBody,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = getattr(application, "voice", None)
    if service is None:
        return JSONResponse(
            {"speech": f"Heard: {body.text}", "intent": None, "tier": 0, "result": None}
        )
    response = await service.handle(body.text)
    return JSONResponse(response.model_dump(mode="json"))
