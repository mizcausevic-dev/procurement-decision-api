"""
Optional audit-stream-py integration.

When the `AUDIT_STREAM_URL` env var is set, this module fires
governance events at `{AUDIT_STREAM_URL}/events` for the moments the
service produces. Best-effort: a failed POST is logged, not raised —
audit-stream outages must never block decision drafting.

Set `AUDIT_STREAM_URL=` (empty) or unset to disable. Set
`AUDIT_STREAM_TIMEOUT_S=2.5` to override the default bounded call timeout.
"""

from __future__ import annotations

import os
import sys
from math import isfinite
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 2.5


def is_enabled() -> bool:
    """True when AUDIT_STREAM_URL is set to a non-empty value."""
    return bool(os.environ.get("AUDIT_STREAM_URL", "").strip())


def base_url() -> str | None:
    """Stripped audit-stream base URL, or None when disabled."""
    raw = os.environ.get("AUDIT_STREAM_URL", "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


def timeout_s() -> float:
    """Configured per-call timeout. Defaults to 2.5s."""
    raw = os.environ.get("AUDIT_STREAM_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
        return min(10.0, max(0.1, value)) if isfinite(value) else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


async def emit(
    client: httpx.AsyncClient,
    *,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """
    Fire one event. Silent no-op when AUDIT_STREAM_URL is unset.

    Failures (timeout, 5xx, connection refused) are swallowed and printed
    to stderr — audit-stream is the consumer, never the dependency.
    """
    url = base_url()
    if url is None:
        return

    body = {
        "kind": kind,
        "source": "procurement-decision-api",
        "payload": payload,
    }
    try:
        response = await client.post(
            f"{url}/events",
            json=body,
            timeout=timeout_s(),
        )
        response.raise_for_status()
    except (httpx.HTTPError, OSError) as err:
        # Best-effort. Print but don't raise.
        # Exception messages may contain a configured URL with credentials.
        print(f"audit-stream emit failed (kind={kind}): {type(err).__name__}", file=sys.stderr, flush=True)
