"""Bearer-token auth (D16), as pure ASGI middleware.

Three ways in, one token: an Authorization header (API callers, ntfy action
buttons), the pihome_token cookie (browsers), or a one-time ?token= query
which sets the cookie and redirects — the kiosk's entry path, since a plain
page navigation cannot carry a header. /healthz and /static are exempt.
"""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "pihome_token"
_EXEMPT_PREFIXES = ("/healthz", "/static/")


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope["path"]
        if path.startswith(_EXEMPT_PREFIXES):
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if self._header_ok(headers) or self._cookie_ok(headers):
            await self._app(scope, receive, send)
            return

        query = parse_qs(scope.get("query_string", b"").decode())
        supplied = query.get("token", [""])[0]
        if supplied and secrets.compare_digest(supplied, self._token):
            remaining = {key: values for key, values in query.items() if key != "token"}
            target = path + (f"?{urlencode(remaining, doseq=True)}" if remaining else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(COOKIE_NAME, supplied, httponly=True, samesite="lax")
            await response(scope, receive, send)
            return

        await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)

    def _header_ok(self, headers: Headers) -> bool:
        auth = headers.get("authorization", "")
        scheme, _, credential = auth.partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(
            credential.strip(), self._token
        )

    def _cookie_ok(self, headers: Headers) -> bool:
        cookie_header = headers.get("cookie")
        if not cookie_header:
            return False
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(COOKIE_NAME)
        return morsel is not None and secrets.compare_digest(morsel.value, self._token)
