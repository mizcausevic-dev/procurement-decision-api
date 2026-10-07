"""
Assemble a Draft Decision Card from a DraftRequest + fetched documents.

This is the heart of the service. Order of operations:

  1. Keep fetched document metadata outside the Decision Card until reviewed.
  2. Keep the draft pending and return a proposed or rubric status separately.
  3. Keep caller conditions for a later authorized review.
  4. Compose a rationale (caller-supplied or generated).
  5. Pack the Decision Card and run Pydantic validation — including the
     superRefine-equivalent rules in the model.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .fetcher import FetchedDocument
from .models import (
    Criteria,
    Decision,
    DecisionCard,
    DecisionMaker,
    DecisionStatus,
    DraftRequest,
    Publication,
    Subject,
)
from .rubric import compose_rationale, infer_status


class DraftError(ValueError):
    """Raised when the request can't be turned into a valid Decision Card."""


def draft_decision_card(
    req: DraftRequest,
    *,
    fetched_documents: list[FetchedDocument],
) -> tuple[DecisionCard, DecisionStatus | None]:
    """
    Build a Decision Card from the request + fetched docs.

    Returns:
      (card, suggested_status)
        card             — the validated DecisionCard instance
        suggested_status — advisory result, never an authorization
    """
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")

    # 1. Fetching a document is not a buyer review. The API returns fetched
    # metadata separately; a later authorized review may populate this field.

    # 2. A rubric suggestion is advisory; never turn it into an approval.
    suggested: DecisionStatus | None = None
    status: DecisionStatus = "pending"
    suggested = req.proposed_status if req.proposed_status is not None else infer_status(req.rubric)

    if req.publication and req.publication.is_public is True:
        raise DraftError("A draft cannot claim publication.is_public=true; publish only after buyer review")

    # 4. rationale
    if req.rationale_template:
        rationale = req.rationale_template
    else:
        rationale = compose_rationale(
            req.rubric,
            status=suggested,
            vendor_name=req.vendor_name,
            product_name=req.product_name,
            documents_count=len(fetched_documents),
        )

    # 5. assemble
    decision = Decision(
        status=status,
        effective_from=req.effective_from,
        effective_until=req.effective_until,
        scope=req.scope,
    )

    subject = Subject(
        vendor_name=req.vendor_name,
        product_name=req.product_name,
        vendor_id=req.vendor_id,
        documents_reviewed=None,
    )

    criteria: Criteria | None = None
    if req.policy_uris or req.rubric:
        criteria = Criteria(
            policy_uris=req.policy_uris,
            rubric=req.rubric if req.rubric else None,
        )

    publication: Publication | None = req.publication

    decision_maker: DecisionMaker | None = req.decision_maker

    card = DecisionCard(
        decision_card_version="0.1",
        decision_id=req.decision_id,
        issued_at=now_iso,
        buyer=req.buyer,
        decision_maker=decision_maker,
        decision=decision,
        subject=subject,
        criteria=criteria,
        conditions=req.conditions,
        rationale=rationale,
        publication=publication,
    )
    return card, suggested
