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
    return httpx.Response(404)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient that intercepts HTTP calls to the mocked vendor."""
    transport = httpx.MockTransport(_vendor_router)
    # Capture the real AsyncClient class BEFORE we patch the symbol; otherwise
    # the factory would call its own patched self and recurse infinitely.
    real_async_client = httpx.AsyncClient

    def _install_mock_client(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=transport, follow_redirects=True)

    # Replace the lifespan's client construction. Lifespan runs on first request.
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _install_mock_client)

    with TestClient(app) as c:
        yield c


class TestMetaEndpoints:
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
        assert body["draft"]["decision"]["status"] == "approved"
        assert len(body["documents_fetched"]) == 1
        assert body["documents_fetched"][0]["type"] == "aeo"
        assert body["documents_fetched"][0]["content_hash"].startswith("sha256:")
        assert body["fetch_errors"] == []
        assert body["inferred_status"] is True

    def test_fail_criterion_requires_conditions(self, client: TestClient) -> None:
        """When inference yields rejected-with-remediation, the request must supply conditions."""
        req = self._base_request(
            rubric=[
                {"id": "ferpa", "result": "fail", "weight": 1.0, "notes": "No DPA"},
            ],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 400
        assert "conditions" in r.json()["detail"]

    def test_fail_criterion_with_conditions_works(self, client: TestClient) -> None:
        req = self._base_request(
            rubric=[{"id": "ferpa", "result": "fail", "weight": 1.0, "notes": "No DPA"}],
            conditions=[{"id": "dpa-remediation", "description": "Sign DPA before re-review."}],
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        assert r.json()["draft"]["decision"]["status"] == "rejected-with-remediation"

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
        assert r.json()["draft"]["decision"]["status"] == "approved-with-conditions"

    def test_proposed_status_overrides_inference(self, client: TestClient) -> None:
        req = self._base_request(
            proposed_status="pending",
            # All-pass rubric would normally yield "approved"
        )
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert body["draft"]["decision"]["status"] == "pending"
        assert body["inferred_status"] is False

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
        assert any("invalid JSON" in e for e in body["fetch_errors"])

    def test_no_fetch_targets_still_works(self, client: TestClient) -> None:
        req = self._base_request(fetch_targets=[])
        r = client.post("/decisions/draft", json=req)
        assert r.status_code == 200
        body = r.json()
        assert body["documents_fetched"] == []

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
        assert body["valid"] is True
        assert body["status"] == "approved"
        assert body["documents_reviewed"] == 0

    def test_missing_required_field(self, client: TestClient) -> None:
        card = self._valid_card()
        del card["rationale"]
        r = client.post("/decisions/validate", json=card)
        assert r.status_code == 422
        body = r.json()
        assert body["detail"]["valid"] is False

    def test_conditional_rule_violation(self, client: TestClient) -> None:
        card = self._valid_card()
        card["decision"]["status"] = "approved-with-conditions"
        # no conditions supplied
        r = client.post("/decisions/validate", json=card)
        assert r.status_code == 422
        body = r.json()
        errs = body["detail"]["errors"]
        assert any("conditions" in str(e.get("msg", "")) for e in errs)
