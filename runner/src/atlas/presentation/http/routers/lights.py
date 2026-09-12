"""The lights surface (spec 5). JSON in, JSON out, bearer auth from the
middleware. Errors are JSONResponses with a status code, like approvals.py:
404 unknown light or scene, 409 feature unsupported, 503 controller down,
422 bad command (FastAPI's own validation of LightCommand).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from atlas.bootstrap.container import Application
from atlas.lights.application.ports import ControllerUnavailable
from atlas.lights.application.service import LightsService, UnknownLight, UnknownScene
from atlas.lights.domain.model import LightCommand, UnsupportedFeature
from atlas.presentation.http.routers._deps import get_application

router = APIRouter(prefix="/api/lights")

_NOT_CONFIGURED = JSONResponse({"detail": "lights are not configured"}, status_code=404)


def _service(application: Application) -> LightsService | None:
    return application.lights


@router.get("")
async def list_lights(
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    return JSONResponse(service.snapshot().model_dump(mode="json"))


@router.get("/scenes")
async def list_scenes(
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    return JSONResponse(
        [{"name": scene.name, "lights": sorted(scene.states)} for scene in service.scenes()]
    )


@router.post("/scenes/{scene_name}/activate")
async def activate_scene(
    scene_name: str,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    try:
        result = await service.activate(scene_name)
    except UnknownScene:
        return JSONResponse({"detail": f"unknown scene {scene_name!r}"}, status_code=404)
    return JSONResponse(result.model_dump(mode="json"))


@router.get("/{name}")
async def get_light(
    name: str,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    try:
        state = service.get(name)
    except UnknownLight:
        return JSONResponse({"detail": f"unknown light {name!r}"}, status_code=404)
    return JSONResponse(state.model_dump(mode="json"))


@router.post("/{name}")
async def set_light(
    name: str,
    body: LightCommand,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    try:
        state = await service.apply(name, body)
    except UnknownLight:
        return JSONResponse({"detail": f"unknown light {name!r}"}, status_code=404)
    except UnsupportedFeature as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except ControllerUnavailable as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    return JSONResponse(state.model_dump(mode="json"))


@router.post("/{name}/toggle")
async def toggle_light(
    name: str,
    application: Annotated[Application, Depends(get_application)],
) -> JSONResponse:
    service = _service(application)
    if service is None:
        return _NOT_CONFIGURED
    try:
        state = await service.toggle(name)
    except UnknownLight:
        return JSONResponse({"detail": f"unknown light {name!r}"}, status_code=404)
    except ControllerUnavailable as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    return JSONResponse(state.model_dump(mode="json"))
