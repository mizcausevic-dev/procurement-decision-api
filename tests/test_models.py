"""
Tests for the Pydantic Decision Card model — including the conditional rules
that mirror the upstream zod schema's superRefine block.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from procurement_decision_api.models import (
    Buyer,
    Condition,
    Decision,
    DecisionCard,
    Publication,
    Subject,
    Withdrawal,
)


def _minimal_card(**overrides) -> DecisionCard:
    """Return a minimal valid DecisionCard, with overrides merged in."""
    defaults = dict(
        decision_card_version="0.1",
        decision_id="TEST-001",
        issued_at="2026-05-14T19:00:00Z",
        buyer=Buyer(name="Test Buyer", type="organization"),
        decision=Decision(status="approved"),
        subject=Subject(vendor_name="Test Vendor"),
        rationale="Test rationale.",
    )
    defaults.update(overrides)
    return DecisionCard(**defaults)


class TestHappyPath:
    def test_minimal_card_validates(self) -> None:
        card = _minimal_card()
        assert card.decision_card_version == "0.1"
        assert card.decision.status == "approved"

    def test_serializes_round_trip(self) -> None:
        card = _minimal_card()
        # Round-trip through dict (the typical API serialization path).
        as_dict = card.model_dump(exclude_none=True)
        card2 = DecisionCard.model_validate(as_dict)
        assert card2.decision_id == card.decision_id


class TestConditionalRules:
    def test_approved_with_conditions_requires_conditions(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _minimal_card(decision=Decision(status="approved-with-conditions"))
        assert "requires at least one entry in conditions" in str(excinfo.value)

    def test_rejected_with_remediation_requires_conditions(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _minimal_card(decision=Decision(status="rejected-with-remediation"))
        assert "requires at least one entry in conditions" in str(excinfo.value)

    def test_approved_with_conditions_accepts_when_conditions_present(self) -> None:
        card = _minimal_card(
            decision=Decision(status="approved-with-conditions"),
            conditions=[Condition(id="C1", description="No training on student data.")],
        )
        assert card.decision.status == "approved-with-conditions"

    def test_withdrawn_requires_withdrawal_block(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _minimal_card(decision=Decision(status="withdrawn"))
        assert "withdrawal block" in str(excinfo.value)

    def test_withdrawn_accepts_when_withdrawal_block_present(self) -> None:
        card = _minimal_card(
            decision=Decision(status="withdrawn"),
            withdrawal=Withdrawal(at="2026-06-01T00:00:00Z", reason="Vendor exited."),
        )
        assert card.decision.status == "withdrawn"

    def test_public_publication_requires_uri(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _minimal_card(publication=Publication(is_public=True))
        assert "publication_uri" in str(excinfo.value)

    def test_public_publication_accepts_when_uri_present(self) -> None:
        card = _minimal_card(
            publication=Publication(
                is_public=True,
                publication_uri="https://example.com/decisions/X.json",
            )
        )
        assert card.publication is not None
        assert card.publication.is_public is True


class TestStrictMode:
    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DecisionCard.model_validate(
                {
                    "decision_card_version": "0.1",
                    "decision_id": "X",
                    "issued_at": "2026-05-14T19:00:00Z",
                    "buyer": {"name": "B", "type": "organization"},
                    "decision": {"status": "approved"},
                    "subject": {"vendor_name": "V"},
                    "rationale": "R",
                    "made_up_field": "nope",  # should be rejected
                }
            )
