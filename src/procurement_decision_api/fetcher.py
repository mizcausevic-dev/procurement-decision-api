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
import ipaddress
import json
import os
import re
from datetime import UTC, datetime

import httpx

from . import __version__
from .models import DocumentReference, FetchTarget

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — well-known docs should never exceed this
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_PATH_RE = re.compile(rb"^/\.well-known/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.json$")


def _allowed_hosts() -> set[str]:
    """Operator-approved exact DNS names. An empty setting denies remote fetches."""
    hosts = {
        part.strip().lower().rstrip(".") for part in os.environ.get("FETCH_ALLOWED_HOSTS", "").split(",")
    }
    hosts.discard("")
    for host in hosts:
        if not _HOST_RE.fullmatch(host) or "." not in host:
            raise ValueError("FETCH_ALLOWED_HOSTS must contain exact DNS hostnames")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("FETCH_ALLOWED_HOSTS must not contain IP addresses")
    return hosts


def _check_targets(targets: list[FetchTarget]) -> None:
    """Reject user-selected destinations that the operator did not approve."""
    allowed = _allowed_hosts()
    if not allowed:
        raise ValueError("Remote fetching requires FETCH_ALLOWED_HOSTS with exact trusted DNS hostnames")
    for target in targets:
        try:
            url = httpx.URL(target.url)
        except httpx.InvalidURL as err:
            raise ValueError("Fetch target URL is invalid") from err
        raw_path = url.raw_path.split(b"?", 1)[0]
        if (
            url.scheme != "https"
            or url.host is None
            or url.host.lower().rstrip(".") not in allowed
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.query
            or url.fragment
            or not _PATH_RE.fullmatch(raw_path)
        ):
            raise ValueError(
                "Fetch targets must use HTTPS on port 443 at an allowed exact hostname, "
                "with an unencoded /.well-known/*.json path and no query or fragment"
            )


class FetchedDocument:
    """Internal carrier for a fetched document reference."""

    __slots__ = ("reference",)

    def __init__(self, reference: DocumentReference) -> None:
        self.reference = reference


def _canonical_hash(parsed: object) -> str:
    """Return `sha256:<hex>` over canonical JSON bytes (sorted keys, no whitespace)."""
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"invalid number {value}")


async def _fetch_one(
    client: httpx.AsyncClient,
    target: FetchTarget,
    *,
    max_bytes: int,
) -> tuple[FetchedDocument | None, str | None]:
    """Fetch one target. Returns (doc, None) on success or (None, error) on failure."""
    try:
        # Stream so an untrusted server cannot make httpx buffer an unbounded body.
        # Redirects are never followed, even if a supplied client enables them.
        async with client.stream("GET", target.url, follow_redirects=False) as response:
            if response.is_redirect:
                return None, f"{target.url}: redirects are not allowed"
            response.raise_for_status()

            if response.headers.get("content-length"):
                try:
                    if int(response.headers["content-length"]) > max_bytes:
                        return None, f"{target.url}: content-length exceeds {max_bytes} bytes"
                except ValueError:
                    pass

            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > max_bytes:
                    return None, f"{target.url}: response body exceeds {max_bytes} bytes"
                body.extend(chunk)
            body_bytes = bytes(body)

        try:
            parsed = json.loads(
                body_bytes.decode("utf-8"),
                parse_constant=_reject_non_json_number,
            )
        except (UnicodeDecodeError, ValueError, RecursionError) as err:
            return None, f"{target.url}: invalid JSON ({err})"

        reference = DocumentReference(
            type=target.type,
            url=target.url,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            content_hash=_canonical_hash(parsed),
        )
        return FetchedDocument(reference), None

    except httpx.TimeoutException:
        return None, f"{target.url}: timeout"
    except httpx.HTTPStatusError as err:
        return None, f"{target.url}: HTTP {err.response.status_code}"
    except httpx.RequestError as err:
        return None, f"{target.url}: {type(err).__name__}: {err}"


async def _fetch_limited(
    client: httpx.AsyncClient, target: FetchTarget, *, timeout_s: float, max_bytes: int
) -> tuple[FetchedDocument | None, str | None]:
    """Cap the entire retrieval, not just each individual network operation."""
    try:
        return await asyncio.wait_for(_fetch_one(client, target, max_bytes=max_bytes), timeout=timeout_s)
    except TimeoutError:
        return None, f"{target.url}: timeout"


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

    _check_targets(targets)

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
            headers={"User-Agent": f"procurement-decision-api/{__version__} (+https://kineticgain.com)"},
        )

    try:
        results = await asyncio.gather(
            *(_fetch_limited(client, t, timeout_s=timeout_s, max_bytes=max_bytes) for t in targets)
        )
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
