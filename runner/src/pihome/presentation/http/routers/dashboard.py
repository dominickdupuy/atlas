"""GET / — the board itself; GET /partials/{panel} — single-panel refresh
(htmx's periodic belt-and-braces poll under the SSE stream)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from pihome.presentation.http.panels import PANEL_NAMES, PanelRenderer
from pihome.presentation.http.routers._deps import get_panels

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def board(panels: Annotated[PanelRenderer, Depends(get_panels)]) -> HTMLResponse:
    return HTMLResponse(await panels.render_page())


@router.get("/partials/{panel}", response_class=HTMLResponse)
async def partial(
    panel: str, panels: Annotated[PanelRenderer, Depends(get_panels)]
) -> HTMLResponse:
    if panel not in PANEL_NAMES:
        raise HTTPException(status_code=404, detail="unknown panel")
    return HTMLResponse(await panels.render(panel))
