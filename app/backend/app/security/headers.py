"""Security response headers and a 500 boundary for the JSON API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
    "Cache-Control": "no-store",
}
HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000")
INTERNAL_ERROR_BODY: dict[str, str] = {"detail": "internal server error"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach SECURITY_HEADERS to every response (HSTS only when `hsts` is enabled).

    Place it outermost so 4xx/5xx and rate-limit responses are covered too. Enable `hsts`
    only when the site is actually served over HTTPS; browsers ignore the header on plain
    HTTP, and a wrong max-age on a later-broken TLS setup locks users out.
    """

    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if self._hsts:
            response.headers.setdefault(*HSTS_HEADER)
        return response


class ErrorBoundaryMiddleware:
    """Pure-ASGI innermost middleware: unhandled exception -> generic JSON 500.

    Sits inside CORS and the security-headers middleware, so the 500 still carries both.
    (Starlette's own Exception handler runs outside every user middleware and would not.)
    The exception is logged server-side; the client sees no detail.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            response = JSONResponse(INTERNAL_ERROR_BODY, status_code=500)
            await response(scope, receive, send)


def sanitize_validation_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only loc/msg/type of pydantic errors: `input` echoes request data (passwords)."""
    return [
        {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
        for e in errors
    ]


__all__ = [
    "ErrorBoundaryMiddleware", "SECURITY_HEADERS", "SecurityHeadersMiddleware",
    "sanitize_validation_errors",
]
