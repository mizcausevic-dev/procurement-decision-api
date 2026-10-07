"""Security and interoperability checks for vendor document fetching."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from procurement_decision_api.fetcher import _canonical_hash, _jcs_hash, fetch_documents
from procurement_decision_api.models import FetchTarget


@pytest.fixture(autouse=True)
def mock_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_host: str) -> list[str]:
        return ["1.1.1.1"]

    monkeypatch.setattr("procurement_decision_api.fetcher._resolve_host_addresses", resolve)


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
async def test_private_dns_answer_rejected_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")

    async def private_dns(_host: str) -> list[str]:
        return ["1.1.1.1", "169.254.169.254"]

    monkeypatch.setattr("procurement_decision_api.fetcher._resolve_host_addresses", private_dns)
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))
    ) as client:
        with pytest.raises(ValueError, match="public IP"):
            await fetch_documents(
                [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")],
                client=client,
            )
    assert requests == []


@pytest.mark.asyncio
async def test_unavailable_dns_is_per_target_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")

    async def unavailable(_host: str) -> list[str]:
        raise OSError("test-only DNS failure")

    monkeypatch.setattr("procurement_decision_api.fetcher._resolve_host_addresses", unavailable)
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))
    ) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")],
            client=client,
        )
    assert docs == []
    assert errors == ["https://vendor.example/.well-known/aeo.json: DNS resolution failed"]
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


@pytest.mark.asyncio
async def test_duplicate_json_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b'{"name":1,"name":2}'))
    async with httpx.AsyncClient(transport=transport) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")], client=client
        )
    assert docs == []
    assert "duplicate JSON object key" in errors[0]


@pytest.mark.asyncio
async def test_out_of_jcs_number_domain_is_per_target_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=b'{"large":9007199254740992}')
    )
    async with httpx.AsyncClient(transport=transport) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")], client=client
        )
    assert docs == []
    assert "outside the RFC 8785 domain" in errors[0]


@pytest.mark.asyncio
async def test_scalar_json_is_not_a_vendor_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "vendor.example")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"null"))
    async with httpx.AsyncClient(transport=transport) as client:
        docs, errors = await fetch_documents(
            [FetchTarget(type="aeo", url="https://vendor.example/.well-known/aeo.json")], client=client
        )
    assert docs == []
    assert "must be a JSON object" in errors[0]


def test_jcs_hash_matches_rust_cross_language_vector() -> None:
    document = {
        "name": "Café",
        "x": 1e-7,
        "negzero": -0.0,
        "דּ": "Hebrew",
        "😀": "Emoji",
        "nested": {"b": 2, "a": 1},
    }
    assert _jcs_hash(document) == ("sha256:2ec69677ffa05c1cb85954219ca2065693bd7b7d0f028ff4fbbc1974baac61a8")


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
