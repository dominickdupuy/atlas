"""NtfyNotifier: approval prompts to the phone (D8 channel).

Uses ntfy's JSON publish API. Action buttons are `http` actions that POST
straight to the D16 decision endpoint — see RequestApprovalService for the
token-in-action tradeoff note.
"""

from __future__ import annotations

import httpx
from pydantic import JsonValue

from pihome.connectors.application.ports import Notification

_PRIORITY = {"default": 3, "high": 4}


class NtfyNotifier:
    def __init__(
        self, base_url: str, topic: str, token: str, timeout_seconds: float = 10.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._topic = topic
        self._token = token
        self._timeout = timeout_seconds

    async def notify(self, notification: Notification) -> None:
        actions: list[JsonValue] = [
            {
                "action": "http",
                "label": action.label,
                "url": action.url,
                "method": action.method,
                "headers": dict(action.headers),
                "body": action.body,
                "clear": True,
            }
            for action in notification.actions
        ]
        payload: dict[str, JsonValue] = {
            "topic": self._topic,
            "title": notification.title,
            "message": notification.body,
            "priority": _PRIORITY[notification.priority],
            "actions": actions,
        }
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._base_url, json=payload, headers=headers)
            response.raise_for_status()
