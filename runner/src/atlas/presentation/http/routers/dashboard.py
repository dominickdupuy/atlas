"""GET /dashboard — the passive ops board (D11), polling /api/status.
GET / — the htmx + SSE board from D17.
GET /partials/{panel} — single-panel refresh for the htmx board.

Both boards are live and read the same services; they differ only in how
the browser gets its updates. See WORKLOG.md for why both exist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from atlas.presentation.http.panels import PANEL_NAMES, PanelRenderer
from atlas.presentation.http.routers._deps import get_panels

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def board(panels: Annotated[PanelRenderer, Depends(get_panels)]) -> HTMLResponse:
    return HTMLResponse(await panels.render_page())


@router.get("/dashboard", response_class=HTMLResponse)
async def ops_board(panels: Annotated[PanelRenderer, Depends(get_panels)]) -> HTMLResponse:
    return HTMLResponse(panels.render_board())


@router.get("/partials/{panel}", response_class=HTMLResponse)
async def partial(
    panel: str, panels: Annotated[PanelRenderer, Depends(get_panels)]
) -> HTMLResponse:
    if panel not in PANEL_NAMES:
        raise HTTPException(status_code=404, detail="unknown panel")
    return HTMLResponse(await panels.render(panel))
