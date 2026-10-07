"""Small process-local abuse controls for the decision endpoints."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from threading import Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BYTES = 512 * 1024
BODY_TIMEOUT_S = 10.0
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_S = 60.0


class RequestBodyLimit:
    """Reject oversized or stalled decision requests before FastAPI parses JSON."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/decisions/"):
            downstream_send = send

            async def no_store(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() != b"cache-control"
                    ]
                    message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
                await downstream_send(message)

            send = no_store

        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
            return
        if declared < 0:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
            return
        if declared > MAX_REQUEST_BYTES:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            return

        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_S):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                            scope, receive, send
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await JSONResponse({"detail": "Request body timeout"}, status_code=408)(scope, receive, send)
            return

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class SlidingWindowLimiter:
    """Cap authenticated operations in one process without retaining client IDs."""

    def __init__(self, limit: int = RATE_LIMIT_REQUESTS, window_s: float = RATE_LIMIT_WINDOW_S) -> None:
        self.limit = limit
        self.window_s = window_s
        self._recent: deque[float] = deque()
        self._lock = Lock()

    def admit(self, now: float | None = None) -> int:
        """Return zero when admitted, otherwise the whole seconds until retry."""
        instant = time.monotonic() if now is None else now
        with self._lock:
            while self._recent and instant - self._recent[0] >= self.window_s:
                self._recent.popleft()
            if len(self._recent) >= self.limit:
                remaining = self.window_s - (instant - self._recent[0])
                return max(1, math.ceil(remaining))
            self._recent.append(instant)
            return 0
