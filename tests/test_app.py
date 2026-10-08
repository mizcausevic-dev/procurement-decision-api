"""
End-to-end tests for the FastAPI app.

Vendor document fetches are mocked using httpx's MockTransport so the tests
don't reach the real internet.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement_decision_api import app as app_module
from procurement_decision_api.app import app
from procurement_decision_api.limits import MAX_REQUEST_BYTES, SlidingWindowLimiter

SAMPLE_AEO_DOC = {
    "aeo_version": "0.1",
    "entity": {
        "id": "https://acmetutor.example/#org",
        "type": "Organization",
        "name": "AcmeTutor Inc.",
        "canonical_url": "https://acmetutor.example/",
    },
    "authority": {"primary_sources": ["https://acmetutor.example/"]},
    "claims": [
        {"id": "tag", "predicate": "description", "value": "AI tutoring", "confidence": "high"},
    ],
}

SAMPLE_TOOL_CARD_DOC = {
    "tool_card_version": "0.1",
    "tool_id": "https://acmetutor.example/tools/lookup_homework",
    "name": "lookup_homework",
    "description": "Look up the assigned homework for a student",
}
TEST_API_TOKEN = "test-only-api-token-32-characters-minimum"


def _vendor_router(request: httpx.Request) -> httpx.Response:
    """Route fake vendor URLs back to canned documents (used by httpx MockTransport)."""
    url = str(request.url)
    if url.endswith("/.well-known/aeo.json"):
        return httpx.Response(200, json=SAMPLE_AEO_DOC)
    if url.endswith("/.well-known/tool-cards/lookup.json"):
        return httpx.Response(200, json=SAMPLE_TOOL_CARD_DOC)
    if url.endswith("/.well-known/missing.json"):
        return httpx.Response(404)
    if url.endswith("/.well-known/bad-json.json"):
        return httpx.Response(
            200, content=b"this is not JSON {", headers={"content-type": "application/json"}
        )
    if url.endswith("/.well-known/redirect.json"):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    return httpx.Response(404)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient that intercepts HTTP calls to the mocked vendor."""
    monkeypatch.setenv("API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("FETCH_ALLOWED_HOSTS", "acmetutor.example")

    async def public_dns(_host: str) -> list[str]:
        return ["1.1.1.1"]

    monkeypatch.setattr("procurement_decision_api.fetcher._resolve_host_addresses", public_dns)
    monkeypatch.setattr(app.state, "rate_limiter", SlidingWindowLimiter())
    transport = httpx.MockTransport(_vendor_router)
    # Capture the real AsyncClient class BEFORE we patch the symbol; otherwise
    # the factory would call its own patched self and recurse infinitely.
    real_async_client = httpx.AsyncClient

    def _install_mock_client(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        assert _kwargs["trust_env"] is False
        return real_async_client(transport=transport, follow_redirects=False)

    # Replace the lifespan's client construction. Lifespan runs on first request.
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _install_mock_client)

    with TestClient(app, headers={"Authorization": f"Bearer {TEST_API_TOKEN}"}) as c:
        yield c


class TestMetaEndpoints:
    def test_synthetic_pilot_rejects_outbound_audit_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROCUREMENT_SYNTHETIC_PILOT", "1")
        monkeypatch.setenv("AUDIT_STREAM_URL", "https://audit.example/events")
        with pytest.raises(RuntimeError, match="forbids outbound AUDIT_STREAM_URL"):
            with TestClient(app):
                pass

    def test_root(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "procurement-decision-api"
        assert "endpoints" in body

    def test_healthz(self, client: TestClient) -> None:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestDraft:
    def _base_request(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "decision_id": "TEST-DEC-001",
            "buyer": {"name": "Springfield USD", "type": "school-district", "jurisdiction": "US-CA"},
            "vendor_name": "AcmeTutor Inc.",
            "product_name": "AcmeTutor 3.0",
            "fetch_targets": [
                {"type": "aeo", "url": "https://acmetutor.example/.well-known/aeo.json"},
            ],
            "rubric": [
                {"id": "ferpa", "result": "pass", "weight": 1.0},
                {"id": "coppa", "result": "pass", "weight": 1.0},
            ],
        }
        body.update(overrides)
        return body

    def test_happy_path_all_pass(self, client: TestClient) -> None:
        r = client.post("/decisions/draft", json=self._base_request())
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["draft"]["decision_id"] == "TEST-DEC-001"
        assert body["draft"]["decision"]["status"] == "pending"
        assert body["suggested_status"] == "approved"
        assert body["draft"]["history"] is None
        assert body["draft"]["subject"]["documents_reviewed"] is None
        assert len(body["documents_fetched"]) == 1
        assert body["documents_fetched"][0]["type"] == "aeo"
        assert body["documents_fetched"][0]["content_hash"].startswith("sha256:")
        assert body["document_hashes"][0]["hash_profile"] == "jcs-rfc8785-v1"
        assert body["document_hashes"][0]["content_hash"].startswith("sha256:")
        assert body["fetch_errors"] == []
        assert r.headers["Cache-Control"] == "no-store"

    def test_fail_criterion_requires_conditions(self, client: TestClient) -> None:
        """A missing condition does not turn a proposed rejection into an official decision."""
        req = self._base_request(
            proposed_status="rejected-with-remediation",
            rubric=[
                {"id": "ferpa", "result": "fail", "weight": 1.0, "notes": "No DPA"},
            ],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["draft"]["decision"]["status"] == "pending"
        assert r.json()["suggested_status"] == "rejected-with-remediation"

    def test_fail_criterion_with_conditions_works(self, client: TestClient) -> None:
        req = self._base_request(
            proposed_status="rejected-with-remediation",
            rubric=[{"id": "ferpa", "result": "fail", "weight": 1.0, "notes": "No DPA"}],
            conditions=[{"id": "dpa-remediation", "description": "Sign DPA before re-review."}],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["draft"]["decision"]["status"] == "pending"
        assert r.json()["suggested_status"] == "rejected-with-remediation"

    def test_partial_criterion_produces_approved_with_conditions(self, client: TestClient) -> None:
        req = self._base_request(
            rubric=[
                {"id": "ferpa", "result": "pass"},
                {"id": "bias-audit", "result": "partial", "notes": "Pending refresh"},
            ],
            conditions=[{"id": "bias-refresh", "description": "Refresh bias audit by 2026-12."}],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["draft"]["decision"]["status"] == "pending"
        assert r.json()["suggested_status"] == "approved-with-conditions"

    def test_proposed_status_overrides_inference(self, client: TestClient) -> None:
        req = self._base_request(
            proposed_status="pending",
            # All-pass rubric would normally yield "approved"
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert body["draft"]["decision"]["status"] == "pending"
        assert body["suggested_status"] == "pending"

    def test_approved_proposal_cannot_approve_unsigned_draft(self, client: TestClient) -> None:
        r = client.post("/decisions/draft", json=self._base_request(proposed_status="approved"))
        assert r.status_code == 200
        assert r.json()["draft"]["decision"]["status"] == "pending"
        assert r.json()["suggested_status"] == "approved"
        assert "Decision status remains pending" in r.json()["draft"]["rationale"]

    def test_fetch_errors_dont_fail_the_draft(self, client: TestClient) -> None:
        req = self._base_request(
            fetch_targets=[
                {"type": "aeo", "url": "https://acmetutor.example/.well-known/aeo.json"},
                {"type": "tool-card", "url": "https://acmetutor.example/.well-known/missing.json"},
            ],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert len(body["documents_fetched"]) == 1
        assert len(body["fetch_errors"]) == 1
        assert "HTTP 404" in body["fetch_errors"][0]

    def test_invalid_json_in_fetched_doc_recorded_as_error(self, client: TestClient) -> None:
        req = self._base_request(
            fetch_targets=[
                {"type": "other", "url": "https://acmetutor.example/.well-known/bad-json.json"},
            ],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert body["documents_fetched"] == []
        assert body["document_hashes"] == []
        assert any("invalid JSON" in e for e in body["fetch_errors"])

    def test_no_fetch_targets_still_works(self, client: TestClient) -> None:
        req = self._base_request(fetch_targets=[])
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert body["documents_fetched"] == []

    def test_synthetic_pilot_blocks_fetch_at_api_boundary(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROCUREMENT_SYNTHETIC_PILOT", "1")

        async def unexpected_dns(_host: str) -> list[str]:
            raise AssertionError("synthetic pilot must not resolve a vendor hostname")

        monkeypatch.setattr("procurement_decision_api.fetcher._resolve_host_addresses", unexpected_dns)
        blocked = client.post("/decisions/draft", json=self._base_request())
        assert blocked.status_code == 400
        assert "synthetic pilot forbids remote vendor fetch" in blocked.json()["detail"]

        allowed = client.post("/decisions/draft", json=self._base_request(fetch_targets=[]))
        assert allowed.status_code == 200
        assert allowed.json()["draft"]["decision"]["status"] == "pending"

    def test_rationale_template_passed_through(self, client: TestClient) -> None:
        req = self._base_request(rationale_template="My custom rationale text.")
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["draft"]["rationale"] == "My custom rationale text."

    def test_content_hash_is_canonical(self, client: TestClient) -> None:
        """The hash should be over canonicalised JSON (sorted keys, no whitespace)."""
        import hashlib

        canonical = json.dumps(SAMPLE_AEO_DOC, sort_keys=True, separators=(",", ":")).encode()
        expected_hash = "sha256:" + hashlib.sha256(canonical).hexdigest()

        r = client.post("/decisions/draft", json=self._base_request())
        assert r.status_code == 200
        body = r.json()
        assert body["documents_fetched"][0]["content_hash"] == expected_hash

    def test_private_fetch_target_rejected(self, client: TestClient) -> None:
        req = self._base_request(fetch_targets=[{"type": "other", "url": "http://127.0.0.1/admin"}])
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 400
        assert "HTTPS" in r.json()["detail"]

    def test_fetch_requires_operator_allowlist(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FETCH_ALLOWED_HOSTS")
        r = client.post("/decisions/draft", json=self._base_request())
        assert r.status_code == 400
        assert "FETCH_ALLOWED_HOSTS" in r.json()["detail"]

    def test_redirect_is_not_followed(self, client: TestClient) -> None:
        req = self._base_request(
            fetch_targets=[{"type": "other", "url": "https://acmetutor.example/.well-known/redirect.json"}]
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["documents_fetched"] == []
        assert "redirects are not allowed" in r.json()["fetch_errors"][0]

    def test_draft_cannot_claim_publication(self, client: TestClient) -> None:
        req = self._base_request(
            publication={"is_public": True, "publication_uri": "https://buyer.example/decision.json"}
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 400
        assert "draft cannot claim" in r.json()["detail"]

    def test_authentication_is_required(self, client: TestClient) -> None:
        r = client.post(
            "/decisions/draft", json=self._base_request(), headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 401
        assert r.headers["Cache-Control"] == "no-store"

    def test_request_body_limit(self, client: TestClient) -> None:
        r = client.post("/decisions/draft", content=b"x" * (MAX_REQUEST_BYTES + 1))
        assert r.status_code == 413

    def test_auth_rate_limit(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app.state, "rate_limiter", SlidingWindowLimiter(limit=2))
        assert client.post("/decisions/draft", json=self._base_request(fetch_targets=[])).status_code == 200
        assert client.post("/decisions/draft", json=self._base_request(fetch_targets=[])).status_code == 200
        blocked = client.post("/decisions/draft", json=self._base_request(fetch_targets=[]))
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1

    def test_missing_configured_token_fails_closed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("API_TOKEN")
        r = client.post("/decisions/draft", json=self._base_request())
        assert r.status_code == 503

    def test_fetch_target_count_is_bounded(self, client: TestClient) -> None:
        req = self._base_request(fetch_targets=self._base_request()["fetch_targets"] * 17)
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 422

    def test_invalid_draft_error_omits_input_value(self, client: TestClient) -> None:
        req = self._base_request()
        req["buyer"] = {"name": "Private Buyer Name", "type": "invalid-type"}
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 422
        assert "Private Buyer Name" not in r.text

    def test_audit_event_omits_identifiers(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict[str, Any]] = []

        async def capture(_client: httpx.AsyncClient, *, kind: str, payload: dict[str, Any]) -> None:
            assert kind == "decision_card_drafted"
            captured.append(payload)

        monkeypatch.setattr(app_module.audit_stream, "emit", capture)
        r = client.post("/decisions/draft", json=self._base_request())
        assert r.status_code == 200
        assert len(captured) == 1
        assert "buyer" not in captured[0]
        assert "vendor" not in captured[0]
        assert "decision_id" not in captured[0]


class TestValidate:
    def _valid_card(self) -> dict[str, Any]:
        return {
            "decision_card_version": "0.1",
            "decision_id": "VAL-001",
            "issued_at": "2026-05-14T19:00:00Z",
            "buyer": {"name": "B", "type": "organization"},
            "decision": {"status": "approved"},
            "subject": {"vendor_name": "V"},
            "rationale": "R.",
        }

    def test_valid_card(self, client: TestClient) -> None:
        r = client.post("/decisions/validate", json=self._valid_card())
        assert r.status_code == 200
        body = r.json()
        assert body["structure_valid"] is True
        assert body["claimed_status"] == "approved"
        assert body["authority_verified"] is False
        assert body["signatures_verified"] is False
        assert body["documents_referenced"] == 0
        assert "buyer" not in body
        assert "vendor" not in body
        assert r.headers["Cache-Control"] == "no-store"

    def test_missing_required_field(self, client: TestClient) -> None:
        card = self._valid_card()
        card["buyer"]["name"] = "Private Buyer Name"
        del card["rationale"]
        r = client.post("/decisions/validate", json=card)
        assert r.status_code == 422
        body = r.json()
        assert body["detail"]["structure_valid"] is False
        assert "Private Buyer Name" not in r.text

    def test_conditional_rule_violation(self, client: TestClient) -> None:
        card = self._valid_card()
        card["decision"]["status"] = "approved-with-conditions"
        # no conditions supplied
        r = client.post("/decisions/validate", json=card)
        assert r.status_code == 422
        body = r.json()
        errs = body["detail"]["errors"]
        assert any("conditions" in str(e.get("msg", "")) for e in errs)

    def test_validate_requires_token(self, client: TestClient) -> None:
        r = client.post(
            "/decisions/validate", json=self._valid_card(), headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 401
