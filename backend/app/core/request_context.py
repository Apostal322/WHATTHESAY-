"""Per-request id and access log, as plain ASGI middleware.

Plain ASGI (not Starlette's BaseHTTPMiddleware) so the app sees the server's own `receive`:
BaseHTTPMiddleware wraps it in a task group that hides a client disconnect from
`Request.is_disconnected()`, which the API uses to cancel model calls nobody waits for.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

log = logging.getLogger("youverify")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)  # every log line for this request carries it
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            request_id_var.reset(token)
