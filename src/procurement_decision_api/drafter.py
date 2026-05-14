"""
Assemble a Draft Decision Card from a DraftRequest + fetched documents.

This is the heart of the service. Order of operations:

  1. Compute documents_reviewed[] from the fetched documents (URL, hash, time).
  2. Decide on a status (caller-supplied or inferred from rubric).
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
    DocumentReference,
    DraftRequest,
    HistoryEvent,
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
) -> tuple[DecisionCard, bool]:
    """
    Build a Decision Card from the request + fetched docs.

    Returns:
      (card, inferred_status_used)
        card                 — the validated DecisionCard instance
        inferred_status_used — True if we inferred status (caller didn't supply)
    """
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")

    # 1. documents_reviewed from fetched + any URLs the caller already pre-staged
    docs_reviewed: list[DocumentReference] = [d.reference for d in fetched_documents]

    # 2. status: caller wins, otherwise infer
    inferred = False
    if req.proposed_status is not None:
        status = req.proposed_status
    else:
        status = infer_status(req.rubric)
        inferred = True

    # 3. conditions check
    if status in ("approved-with-conditions", "rejected-with-remediation"):
        if not req.conditions:
            raise DraftError(
                f"decision.status={status} requires conditions, "
                "but the request did not supply any. Either provide conditions "
                "or change proposed_status / rubric so a different status is inferred."
            )

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

    history = [
        HistoryEvent(event="review_started", at=now_iso, actor="procurement-decision-api"),
        HistoryEvent(event="documents_collected", at=now_iso, actor="procurement-decision-api"),
        HistoryEvent(event="review_completed", at=now_iso, actor="procurement-decision-api"),
        HistoryEvent(
            event=status if status in _HISTORY_STATUS_EVENTS else "review_completed",
            at=now_iso,
            actor="procurement-decision-api",
            note="Draft produced by procurement-decision-api; review before signing.",
        ),
    ]

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
        history=history,
        publication=publication,
    )
    return card, inferred


# History event names that match DecisionStatus values 1:1.
_HISTORY_STATUS_EVENTS = {
    "approved",
    "approved-with-conditions",
    "rejected",
    "rejected-with-remediation",
    "pending",
    "withdrawn",
    "expired",
}
