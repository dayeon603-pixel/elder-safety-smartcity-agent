"""Request body size caps per path prefix (pure ASGI, innermost middleware).

A declared Content-Length over the cap is refused with 413 before the body is read; a chunked or
lying body is cut off the moment the running count exceeds the cap (413 if nothing was sent yet).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

HTTP_413 = 413
TOO_LARGE_BODY = {"detail": "request body too large"}


class BodyTooLarge(Exception):
    """Raised from the wrapped `receive` when the streamed body exceeds its cap."""


class BodyLimitMiddleware:
    """`limits` maps a path prefix to a byte cap (first match wins); unmatched paths use `default`."""

    def __init__(self, app: ASGIApp, limits: Sequence[tuple[str, int]], default: int) -> None:
        self.app = app
        self._limits = tuple(limits)
        self._default = default

    def cap_for(self, path: str) -> int:
        for prefix, cap in self._limits:
            if path.startswith(prefix):
                return cap
        return self._default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cap = self.cap_for(scope["path"])
        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > cap:
            await JSONResponse(TOO_LARGE_BODY, status_code=HTTP_413)(scope, receive, send)
            return
        seen = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            logger.warning("body over %d bytes on %s", cap, scope["path"])
            if started:
                raise
            await JSONResponse(TOO_LARGE_BODY, status_code=HTTP_413)(scope, receive, send)


__all__ = ["BodyLimitMiddleware", "BodyTooLarge"]
