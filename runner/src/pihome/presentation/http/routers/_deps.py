"""Router dependencies: pull the composition root off app.state."""

from __future__ import annotations

from fastapi import Request

from pihome.bootstrap.container import Application
from pihome.presentation.http.panels import PanelRenderer


def get_application(request: Request) -> Application:
    application: Application = request.app.state.application
    return application


def get_panels(request: Request) -> PanelRenderer:
    panels: PanelRenderer = request.app.state.panels
    return panels
