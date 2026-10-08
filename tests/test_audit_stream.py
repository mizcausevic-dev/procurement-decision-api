"""Tests for the audit-stream-py integration."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from procurement_decision_api import audit_stream

TEST_AUDIT_TOKEN = "a" * 32


@pytest.fixture(autouse=True)
def configured_audit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_STREAM_TOKEN", TEST_AUDIT_TOKEN)


class TestConfig:
    def test_disabled_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_URL", raising=False)
        assert audit_stream.is_enabled() is False
        assert audit_stream.base_url() is None

    def test_disabled_when_env_var_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "   ")
        assert audit_stream.is_enabled() is False
        assert audit_stream.base_url() is None

    def test_enabled_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093")
        assert audit_stream.is_enabled() is True
        assert audit_stream.base_url() == "http://localhost:8093"

    def test_trailing_slash_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093/")
        assert audit_stream.base_url() == "http://localhost:8093"

    def test_base_and_endpoint_url_normalize_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093/private/")
        assert audit_stream.events_url() == "http://localhost:8093/private/events"
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093/private/events/")
        assert audit_stream.events_url() == "http://localhost:8093/private/events"

    @pytest.mark.parametrize("value", ["http://user:password@audit.local", "http://audit.local?token=x"])
    def test_url_credentials_are_rejected(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", value)
        assert audit_stream.events_url() is None

    def test_timeout_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_TIMEOUT_S", raising=False)
        assert audit_stream.timeout_s() == audit_stream.DEFAULT_TIMEOUT_S

    def test_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_TIMEOUT_S", "5.0")
        assert audit_stream.timeout_s() == 5.0

    def test_timeout_bad_value_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_TIMEOUT_S", "not-a-number")
        assert audit_stream.timeout_s() == audit_stream.DEFAULT_TIMEOUT_S

    def test_timeout_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_TIMEOUT_S", "infinity")
        assert audit_stream.timeout_s() == audit_stream.DEFAULT_TIMEOUT_S
        monkeypatch.setenv("AUDIT_STREAM_TIMEOUT_S", "600")
        assert audit_stream.timeout_s() == 10.0


class TestEmit:
    @pytest.mark.asyncio
    async def test_emit_is_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_URL", raising=False)
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await audit_stream.emit(client, kind="decision_card_drafted", payload={"x": 1})
        assert captured == []  # never reached

    @pytest.mark.asyncio
    async def test_emit_posts_to_events_endpoint_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local/")
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "http://audit.local/events"
            assert request.method == "POST"
            assert request.headers["Authorization"] == f"Bearer {TEST_AUDIT_TOKEN}"
            import json

            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201, json={"event_id": 1})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await audit_stream.emit(
                client,
                kind="decision_card_drafted",
                payload={"decision_id": "DEC-1", "vendor": "AcmeTutor"},
            )
        assert len(captured) == 1
        body = captured[0]
        assert body["kind"] == "decision_card_drafted"
        assert body["source"] == "procurement-decision-api"
        assert body["payload"]["decision_id"] == "DEC-1"

    @pytest.mark.asyncio
    async def test_emit_swallows_server_error_silently(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local/")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # Must not raise.
            await audit_stream.emit(client, kind="decision_card_drafted", payload={})
        captured = capsys.readouterr()
        assert "audit-stream emit failed" in captured.err
        assert "http://audit.local" not in captured.err

    @pytest.mark.asyncio
    async def test_emit_swallows_connection_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://nope.local/")

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # Must not raise.
            await audit_stream.emit(client, kind="decision_card_drafted", payload={})

    @pytest.mark.asyncio
    async def test_missing_token_prevents_outbound_request_and_logs_without_secret(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local")
        monkeypatch.setenv("AUDIT_STREAM_TOKEN", "short-private-token")
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await audit_stream.emit(client, kind="decision_card_drafted", payload={})
        assert captured == []
        error = capsys.readouterr().err
        assert "InvalidConfiguration" in error
        assert "short-private-token" not in error

    @pytest.mark.asyncio
    async def test_rejected_token_is_failed_delivery_without_secret_log(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local/events")

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "http://audit.local/events"
            assert request.headers["Authorization"] == f"Bearer {TEST_AUDIT_TOKEN}"
            return httpx.Response(401)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await audit_stream.emit(client, kind="decision_card_drafted", payload={})
        error = capsys.readouterr().err
        assert "HTTPStatusError" in error
        assert "status=401" in error
        assert TEST_AUDIT_TOKEN not in error
