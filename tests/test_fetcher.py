"""Security and interoperability checks for vendor document fetching."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from procurement_decision_api.fetcher import _canonical_hash, fetch_documents
from procurement_decision_api.models import FetchTarget


class _OversizeStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_read = 0

    async def __aiter__(self):
        for chunk in (b"12345678", b"abcdefgh", b"must-not-be-read"):
            self.chunks_read += 1
            yield chunk

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_stream_stops_after_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    stream = _OversizeStream()
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream))
    async with httpx.AsyncClient(transport=transport) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")],
            client=client,
            max_bytes=10,
        )
    assert docs == []
    assert "response body exceeds 10 bytes" in errors[0]
    assert stream.chunks_read == 2


@pytest.mark.asyncio
async def test_query_parameter_is_rejected_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="no query"):
            await fetch_documents(
                [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json?token=secret")],
                client=client,
            )
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/%2e%2e/admin.json",
        "/.well-known/%2fadmin.json",
        "/.well-known/%5cadmin.json",
        "/.well-known/../admin.json",
    ],
)
async def test_path_traversal_rejected_before_network(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unencoded"):
            await fetch_documents(
                [FetchTarget(type="aeo", url=f"https://vendor.example{path}")], client=client
            )
    assert requests == []


@pytest.mark.asyncio
async def test_total_fetch_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")

    async def stalled(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")],
            client=client,
            timeout_s=0.01,
        )
    assert docs == []
    assert errors == ["https://vendor.example/.well-known/aeo.json: timeout"]


@pytest.mark.asyncio
async def test_non_json_number_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b'{"score":NaN}'))
    async with httpx.AsyncClient(transport=transport) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")], client=client
        )
    assert docs == []
    assert "invalid JSON" in errors[0]


@pytest.mark.xfail(
    strict=True, reason="Published Python v0.1.1 and Rust hashes use different Unicode serialization"
)
def test_unicode_hash_parity_with_rust() -> None:
    assert _canonical_hash({"name": "Café"}) == (
        "sha256:659906f125d844f7081786e4a1cba739414e49a9b9061d80ce09c691b5f56602"
    )


@pytest.mark.xfail(
    strict=True, reason="Published Python v0.1.1 and Rust hashes use different exponent serialization"
)
def test_exponent_hash_parity_with_rust() -> None:
    assert _canonical_hash({"x": 1e-7}) == (
        "sha256:43c8e92bd5552bd45030718eb9366d6d6500623793c248666d77dca01ba337c0"
    )
