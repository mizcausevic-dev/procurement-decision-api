"""Truth boundary between fetched source documents and buyer review."""

from procurement_decision_api.drafter import draft_decision_card
from procurement_decision_api.fetcher import FetchedDocument
from procurement_decision_api.models import DocumentHash, DocumentReference, DraftRequest


def test_fetched_document_is_not_marked_reviewed() -> None:
    request = DraftRequest.model_validate(
        {
            "decision_id": "DRAFT-001",
            "buyer": {"name": "Example buyer", "type": "organization"},
            "vendor_name": "Example vendor",
            "rubric": [{"id": "evidence", "result": "pass"}],
        }
    )
    fetched = FetchedDocument(
        reference=DocumentReference(
            type="aeo",
            url="https://vendor.example/aeo.json",
            fetched_at="2026-10-07T00:00:00Z",
            content_hash="sha256:" + "a" * 64,
        ),
        document_hash=DocumentHash(
            type="aeo",
            url="https://vendor.example/aeo.json",
            hash_profile="jcs-rfc8785-v1",
            content_hash="sha256:" + "b" * 64,
        ),
    )

    card, suggested = draft_decision_card(request, fetched_documents=[fetched])

    assert card.decision.status == "pending"
    assert suggested == "approved"
    assert card.subject.documents_reviewed is None
    assert "1 fetched vendor declaration" in card.rationale
