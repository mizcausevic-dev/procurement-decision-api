"""
Pydantic v2 models mirroring the AI Procurement Decision Card v0.1 schema.

Source of truth: https://github.com/mizcausevic-dev/ai-procurement-decision-spec
Schema:         decision-card.schema.json (JSON Schema 2020-12)

The conditional rules in the upstream schema's superRefine block are mirrored
here as model_validator checks so the API rejects ill-formed cards at the
same point the npm/zod validator would.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Enumerations — match the JSON Schema exactly so Decision Cards produced
# here validate identically against kg-validate-action and the hosted validator.
# ---------------------------------------------------------------------------

BuyerType = Literal[
    "organization",
    "agency",
    "school-district",
    "school",
    "hospital",
    "health-system",
    "research-institution",
    "auditor",
    "individual",
]

DecisionStatus = Literal[
    "approved",
    "approved-with-conditions",
    "rejected",
    "rejected-with-remediation",
    "pending",
    "withdrawn",
    "expired",
]

DocumentType = Literal[
    "aeo",
    "prompt-provenance",
    "agent-card",
    "ai-evidence",
    "tool-card",
    "tutor-card",
    "student-ai-disclosure",
    "classroom-aup",
    "clinical-ai-card",
    "incident-card",
    "other",
]

RubricResult = Literal["pass", "pass-with-condition", "partial", "fail", "n/a"]

ConditionEnforcement = Literal[
    "contractual",
    "technical",
    "audit",
    "self-attestation",
    "regulatory",
    "other",
]

HistoryEventKind = Literal[
    "review_started",
    "documents_collected",
    "review_completed",
    "approved",
    "approved-with-conditions",
    "rejected",
    "rejected-with-remediation",
    "pending",
    "withdrawn",
    "expired",
    "appealed",
    "amended",
    "other",
]

SignatureMethod = Literal[
    "digital",
    "wet-ink",
    "electronic-attestation",
    "cryptographic",
    "other",
]


class StrictModel(BaseModel):
    """Reject unknown fields the same way the upstream zod schema does."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Inner structures
# ---------------------------------------------------------------------------


class Buyer(StrictModel):
    name: str = Field(..., min_length=1)
    type: BuyerType
    category: str | None = None
    jurisdiction: str | None = None
    url: str | None = None
    contact: str | None = None
    id: str | None = None


class DecisionMaker(StrictModel):
    role: str = Field(..., min_length=1)
    name: str | None = None
    department: str | None = None
    authority: str | None = None


class Decision(StrictModel):
    status: DecisionStatus
    effective_from: str | None = None
    effective_until: str | None = None
    scope: str | None = None


class DocumentReference(StrictModel):
    type: DocumentType
    url: str
    fetched_at: str | None = None
    content_hash: str | None = None
    version: str | None = None


class Subject(StrictModel):
    vendor_name: str = Field(..., min_length=1)
    product_name: str | None = None
    vendor_id: str | None = None
    documents_reviewed: list[DocumentReference] | None = None


class RubricCriterion(StrictModel):
    id: str = Field(..., min_length=1)
    description: str | None = None
    weight: float | None = Field(default=None, ge=0, le=1)
    result: RubricResult
    notes: str | None = None


class Criteria(StrictModel):
    policy_uris: list[str] | None = None
    rubric: list[RubricCriterion] | None = None


class Condition(StrictModel):
    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    enforcement: ConditionEnforcement | None = None
    violation_response: str | None = None
    verification_uri: str | None = None


class HistoryEvent(StrictModel):
    event: HistoryEventKind
    at: str
    actor: str | None = None
    note: str | None = None


class Appeals(StrictModel):
    deadline: str | None = None
    process_uri: str | None = None
    contact: str | None = None


class Publication(StrictModel):
    publication_uri: str | None = None
    is_public: bool | None = None
    visibility_notes: str | None = None


class Signature(StrictModel):
    signer: str = Field(..., min_length=1)
    signed_at: str
    method: SignatureMethod | None = None
    key_uri: str | None = None
    signature_value: str | None = None


class Withdrawal(StrictModel):
    at: str
    reason: str = Field(..., min_length=1)
    replaces: str | None = None


# ---------------------------------------------------------------------------
# Top-level Decision Card
# ---------------------------------------------------------------------------


class DecisionCard(StrictModel):
    """
    AI Procurement Decision Card — v0.1 conformant.

    Conditional rules enforced via model_validator:
      - status=approved-with-conditions or rejected-with-remediation
        requires non-empty conditions
      - status=withdrawn requires a withdrawal block
      - publication.is_public=True requires publication_uri
    """

    decision_card_version: Literal["0.1"] = "0.1"
    decision_id: str = Field(..., min_length=1, max_length=128)
    issued_at: str
    buyer: Buyer
    decision_maker: DecisionMaker | None = None
    decision: Decision
    subject: Subject
    criteria: Criteria | None = None
    conditions: list[Condition] | None = None
    rationale: str = Field(..., min_length=1)
    history: list[HistoryEvent] | None = None
    appeals: Appeals | None = None
    publication: Publication | None = None
    signatures: list[Signature] | None = None
    withdrawal: Withdrawal | None = None

    @model_validator(mode="after")
    def _enforce_conditional_rules(self) -> DecisionCard:
        status = self.decision.status

        if status in ("approved-with-conditions", "rejected-with-remediation"):
            if not self.conditions:
                raise ValueError(f"decision.status={status} requires at least one entry in conditions")

        if status == "withdrawn" and self.withdrawal is None:
            raise ValueError("decision.status=withdrawn requires a withdrawal block (at + reason)")

        if self.publication and self.publication.is_public is True:
            if not self.publication.publication_uri:
                raise ValueError("publication.is_public=true requires publication.publication_uri")

        return self


# ---------------------------------------------------------------------------
# API request / response models — distinct from the Decision Card itself
# ---------------------------------------------------------------------------


class FetchTarget(StrictModel):
    """Where to find a single vendor declaration document."""

    type: DocumentType
    url: str


class DraftRequest(StrictModel):
    """
    Inputs to POST /decisions/draft.

    Provide all the human-decided pieces (buyer, decision_maker, vendor identity,
    criteria, rationale). The service fetches the vendor's documents, evaluates
    the rubric, and returns a Draft Decision Card you can review and sign.
    """

    decision_id: str = Field(..., min_length=1, max_length=128)
    buyer: Buyer
    decision_maker: DecisionMaker | None = None

    vendor_name: str = Field(..., min_length=1)
    product_name: str | None = None
    vendor_id: str | None = None
    fetch_targets: list[FetchTarget] = Field(default_factory=list)

    policy_uris: list[str] | None = None
    rubric: list[RubricCriterion]
    rationale_template: str | None = Field(
        default=None,
        description=(
            "Optional human-authored rationale to embed in the draft. "
            "If omitted, the service composes one from the rubric results."
        ),
    )

    proposed_status: DecisionStatus | None = Field(
        default=None,
        description=(
            "If set, the service uses this. "
            "If omitted, the service infers from rubric results: any fail -> rejected; "
            "any partial / pass-with-condition -> approved-with-conditions; "
            "all pass -> approved."
        ),
    )

    scope: str | None = None
    effective_from: str | None = None
    effective_until: str | None = None
    conditions: list[Condition] | None = None
    publication: Publication | None = None


class DraftResponse(StrictModel):
    """Response from POST /decisions/draft."""

    draft: DecisionCard
    documents_fetched: list[DocumentReference]
    fetch_errors: list[str] = Field(default_factory=list)
    inferred_status: bool = False
