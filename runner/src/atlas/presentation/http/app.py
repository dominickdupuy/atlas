"""FastAPI application factory. The lifespan owns the background tasks:
scheduler, MQTT connection loop, approval sweep, heartbeat."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from atlas.bootstrap.container import Application
from atlas.presentation.http.auth import BearerAuthMiddleware
from atlas.presentation.http.panels import PanelRenderer
from atlas.presentation.http.routers import approvals, dashboard, events, health, jobs

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(application: Application) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await application.start_persistence()
        tasks = [
            asyncio.create_task(application.scheduler.run(), name="scheduler"),
            asyncio.create_task(application.mqtt.run(), name="mqtt"),
            asyncio.create_task(application.sweep.run(), name="approval-sweep"),
            asyncio.create_task(application.health.run(), name="heartbeat"),
        ]
        logger.info("atlas runner up (profile=%s)", application.settings.profile)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await application.db.close()

    app = FastAPI(title="atlas", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.application = application
    app.state.panels = PanelRenderer(application)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(approvals.router)
    app.include_router(jobs.router)
    app.include_router(events.router)
    app.include_router(dashboard.router)

    app.add_middleware(BearerAuthMiddleware, token=application.settings.api_token)
    return app
