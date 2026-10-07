"""Boundary behavior for the process-local request controls."""

from __future__ import annotations

from typing import Any

import pytest

from procurement_decision_api.limits import MAX_REQUEST_BYTES, RequestBodyLimit, SlidingWindowLimiter


@pytest.mark.asyncio
async def test_streamed_body_limit_ignores_false_content_length() -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"a" * MAX_REQUEST_BYTES, "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ]
    )
    sent: list[dict[str, Any]] = []
    reached_app = False

    async def downstream(_scope: Any, _receive: Any, _send: Any) -> None:
        nonlocal reached_app
        reached_app = True

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/decisions/draft",
        "headers": [(b"content-length", b"1")],
    }
    await RequestBodyLimit(downstream)(scope, receive, send)
    assert reached_app is False
    assert sent[0]["status"] == 413
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


def test_sliding_window_recovers_after_one_minute() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_s=60)
    assert limiter.admit(now=100.0) == 0
    assert limiter.admit(now=101.0) == 0
    assert limiter.admit(now=102.0) == 58
    assert limiter.admit(now=160.0) == 0
