"""LightsTools: the lights service as the connectors gateway's in-process
`lights.*` server (D25). Voice and, later, jobs call lights through here."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue, ValidationError

from atlas.lights.application.registry import UnknownTarget
from atlas.lights.application.service import (
    CONTROLLER_UNAVAILABLE,
    ApplyOutcome,
    LightsService,
    UnknownScene,
)
from atlas.lights.domain.model import LightCommand


class LightsTools:
    def __init__(self, service: LightsService) -> None:
        self._service = service

    async def set_lights(
        self, targets: Sequence[str], command: dict[str, JsonValue], *, toggle: bool = False
    ) -> dict[str, JsonValue]:
        applied: list[str] = []
        failed: list[str] = []
        errors: dict[str, JsonValue] = {}
        states: dict[str, JsonValue] = {}
        names: list[str] = []
        for target in targets:
            try:
                names.extend(self._service.resolve(target))
            except UnknownTarget as exc:
                failed.append(target)
                errors[target] = str(exc)
        parsed: LightCommand | None = None
        if not toggle:
            try:
                parsed = LightCommand.model_validate(command)
            except ValidationError as exc:
                return {
                    "applied": [],
                    "failed": cast(list[JsonValue], list(dict.fromkeys(names + failed))),
                    "errors": {**errors, "command": str(exc)},
                    "states": {},
                }

        async def one(name: str) -> tuple[str, ApplyOutcome | Exception]:
            try:
                if toggle:
                    return name, await self._service.toggle(name)
                assert parsed is not None
                return name, await self._service.apply(name, parsed)
            except Exception as exc:
                return name, exc

        for name, outcome in await asyncio.gather(*(one(n) for n in dict.fromkeys(names))):
            if isinstance(outcome, Exception):
                failed.append(name)
                errors[name] = str(outcome)
                continue
            states[name] = outcome.state.model_dump(mode="json")
            if outcome.confirmed:
                applied.append(name)
            else:
                # The bulb may still have obeyed; the device did not confirm in time.
                failed.append(name)
                errors[name] = outcome.error or "unconfirmed"
        payload: dict[str, JsonValue] = {
            "applied": cast(list[JsonValue], applied),
            "failed": cast(list[JsonValue], failed),
            "errors": errors,
            "states": states,
        }
        if not applied and errors and all(v == CONTROLLER_UNAVAILABLE for v in errors.values()):
            payload["error"] = CONTROLLER_UNAVAILABLE
        return payload

    async def activate_scene(self, name: str) -> dict[str, JsonValue]:
        try:
            result = await self._service.activate(name)
        except UnknownScene as exc:
            return {"scene": name, "applied": [], "failed": [], "errors": {"scene": str(exc)}}
        dumped = dict(result.model_dump(mode="json"))
        if not result.applied and result.snapshot.error == CONTROLLER_UNAVAILABLE:
            dumped["error"] = CONTROLLER_UNAVAILABLE
        return dumped

    def state(self) -> dict[str, JsonValue]:
        return dict(self._service.snapshot().model_dump(mode="json"))
