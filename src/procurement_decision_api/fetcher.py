"""
Vendor document fetcher.

Fetches each FetchTarget URL with httpx, computes a sha256 content_hash over the
canonicalised JSON (sorted keys, no whitespace), and returns DocumentReference
records suitable for inclusion in the Decision Card's subject.documents_reviewed.

Errors are collected per-document; one failed fetch doesn't fail the whole draft.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import httpx

from .models import DocumentReference, FetchTarget

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — well-known docs should never exceed this


class FetchedDocument:
    """Internal carrier for a successfully-fetched document.

    Provides both the DocumentReference (for the Decision Card) and the parsed
    JSON body (for rubric evaluation).
    """

    __slots__ = ("body", "reference")

    def __init__(self, reference: DocumentReference, body: object) -> None:
        self.reference = reference
        self.body = body


def _canonical_hash(parsed: object) -> str:
    """Return `sha256:<hex>` over canonical JSON bytes (sorted keys, no whitespace)."""
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


async def _fetch_one(
    client: httpx.AsyncClient,
    target: FetchTarget,
    *,
    max_bytes: int,
) -> tuple[FetchedDocument | None, str | None]:
    """Fetch one target. Returns (doc, None) on success or (None, error) on failure."""
    try:
        response = await client.get(target.url)
        response.raise_for_status()

        # Cap body size to protect the service from oversized responses.
        if response.headers.get("content-length"):
            try:
                if int(response.headers["content-length"]) > max_bytes:
                    return None, f"{target.url}: content-length exceeds {max_bytes} bytes"
            except ValueError:
                pass

        body_bytes = response.content
        if len(body_bytes) > max_bytes:
            return None, f"{target.url}: response body exceeds {max_bytes} bytes"

        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            return None, f"{target.url}: invalid JSON ({err})"

        reference = DocumentReference(
            type=target.type,
            url=target.url,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            content_hash=_canonical_hash(parsed),
        )
        return FetchedDocument(reference, parsed), None

    except httpx.TimeoutException:
        return None, f"{target.url}: timeout"
    except httpx.HTTPStatusError as err:
        return None, f"{target.url}: HTTP {err.response.status_code}"
    except httpx.RequestError as err:
        return None, f"{target.url}: {type(err).__name__}: {err}"


async def fetch_documents(
    targets: list[FetchTarget],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[FetchedDocument], list[str]]:
    """
    Fetch every target concurrently. Returns (docs_fetched, fetch_errors).

    Tests can pass a pre-configured `client` to mock vendor responses.
    """
    if not targets:
        return [], []

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=True,
            headers={"User-Agent": "procurement-decision-api/0.1.0 (+https://kineticgain.com)"},
        )

    try:
        results = await asyncio.gather(*(_fetch_one(client, t, max_bytes=max_bytes) for t in targets))
    finally:
        if own_client:
            await client.aclose()

    docs: list[FetchedDocument] = []
    errors: list[str] = []
    for doc, err in results:
        if doc is not None:
            docs.append(doc)
        if err is not None:
            errors.append(err)
    return docs, errors
