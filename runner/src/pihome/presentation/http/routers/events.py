"""GET /events — the SSE stream (D17).

Each domain event re-renders its affected panels and emits them as named
SSE events whose data is an HTML fragment; htmx's sse-swap drops them into
place. Fed by the in-process stream, so the board survives a broker outage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sse_starlette import EventSourceResponse, ServerSentEvent

from pihome.bootstrap.container import Application
from pihome.presentation.http.panels import PanelRenderer, panels_for_event
from pihome.presentation.http.routers._deps import get_application, get_panels

router = APIRouter()


@router.get("/events")
async def events(
    application: Annotated[Application, Depends(get_application)],
    panels: Annotated[PanelRenderer, Depends(get_panels)],
) -> EventSourceResponse:
    async def stream() -> AsyncIterator[ServerSentEvent]:
        async for event in application.stream.subscribe():
            for panel in panels_for_event(event):
                # One SSE message per line of data; fragments are collapsed
                # to a single line so multi-line HTML survives the framing.
                html = await panels.render(panel)
                yield ServerSentEvent(event=panel, data=html.replace("\n", ""))

    return EventSourceResponse(stream())
