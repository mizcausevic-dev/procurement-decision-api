"""
Assemble a Draft Decision Card from a DraftRequest + fetched documents.

This is the heart of the service. Order of operations:

  1. Compute documents_reviewed[] from the fetched documents (URL, hash, time).
  2. Keep the draft pending unless the caller explicitly proposes a status.
  3. If status requires conditions and the caller supplied none, raise a
     400-equivalent error (the caller should review the rubric and fill them in).
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
    DocumentReference,
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

    # 1. The schema calls these documents_reviewed; this service only fetched them.
    docs_reviewed: list[DocumentReference] = [d.reference for d in fetched_documents]

    # 2. A rubric suggestion is advisory; never turn it into an approval.
    suggested: DecisionStatus | None = None
    if req.proposed_status is not None:
        status = req.proposed_status
    else:
        status = "pending"
        suggested = infer_status(req.rubric)

    # 3. conditions check
    if status in ("approved-with-conditions", "rejected-with-remediation"):
        if not req.conditions:
            raise DraftError(
                f"decision.status={status} requires conditions, "
                "but the request did not supply any. Provide conditions "
                "or choose a different proposed_status."
            )

    if req.publication and req.publication.is_public is True:
        raise DraftError("A draft cannot claim publication.is_public=true; publish only after buyer review")

    # 4. rationale
    if req.rationale_template:
        rationale = req.rationale_template
    else:
        rationale = compose_rationale(
            req.rubric,
            status=status,
            vendor_name=req.vendor_name,
            product_name=req.product_name,
            documents_count=len(docs_reviewed),
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
        documents_reviewed=docs_reviewed if docs_reviewed else None,
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
