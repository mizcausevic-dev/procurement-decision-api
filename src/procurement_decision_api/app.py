"""
FastAPI app — three endpoints.

  GET  /                  service info
  GET  /healthz           liveness probe
  POST /decisions/draft   produce a Draft Decision Card
  POST /decisions/validate validate an existing Decision Card

Run locally:
  uvicorn procurement_decision_api.app:app --reload --port 8088

Or via the supplied Docker image.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError

from . import __version__
from .drafter import DraftError, draft_decision_card
from .fetcher import DEFAULT_TIMEOUT_S, fetch_documents
from .models import DecisionCard, DraftRequest, DraftResponse


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Hold a single shared httpx.AsyncClient for the lifetime of the app."""
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_S),
        follow_redirects=True,
        headers={"User-Agent": f"procurement-decision-api/{__version__} (+https://kineticgain.com)"},
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(
    title="procurement-decision-api",
    version=__version__,
    description=(
        "Drafts AI Procurement Decision Cards (Kinetic Gain Protocol Suite spec #11) "
        "from a buyer rubric and a set of vendor Suite documents."
    ),
    lifespan=_lifespan,
)


@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    """Service info + relevant links."""
    return {
        "name": "procurement-decision-api",
        "version": __version__,
        "description": (
            "FastAPI service that drafts AI Procurement Decision Cards "
            "from a buyer rubric and vendor Suite documents."
        ),
        "spec": "https://github.com/mizcausevic-dev/ai-procurement-decision-spec",
        "suite": "https://suite.kineticgain.com/",
        "nist_crosswalk": "https://suite.kineticgain.com/docs/nist-rmf-crosswalk.md",
        "endpoints": {
            "GET  /": "this page",
            "GET  /healthz": "liveness probe",
            "POST /decisions/draft": "produce a Draft Decision Card",
            "POST /decisions/validate": "validate an existing Decision Card",
            "GET  /openapi.json": "machine-readable API schema",
            "GET  /docs": "interactive API documentation",
        },
    }


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/decisions/draft",
    response_model=DraftResponse,
    tags=["decisions"],
    responses={
        400: {"description": "Draft inputs are invalid (e.g. status requires conditions)."},
    },
)
async def draft(request: DraftRequest) -> DraftResponse:
    """
    Produce a Draft Decision Card.

    Pipeline:

      1. Fetch every URL in `fetch_targets` (concurrently, with hash + timestamp).
      2. Infer `decision.status` from the rubric (unless `proposed_status` is given).
      3. Compose a rationale (unless `rationale_template` is given).
      4. Assemble + validate the Decision Card.

    Returns the draft plus the list of documents that were successfully fetched
    and any per-target fetch errors.
    """
    http_client: httpx.AsyncClient = app.state.http_client
    fetched, errors = await fetch_documents(request.fetch_targets, client=http_client)

    try:
        card, inferred = draft_decision_card(request, fetched_documents=fetched)
    except DraftError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    return DraftResponse(
        draft=card,
        documents_fetched=[d.reference for d in fetched],
        fetch_errors=errors,
        inferred_status=inferred,
    )


@app.post(
    "/decisions/validate",
    tags=["decisions"],
    responses={
        200: {"description": "Card is valid."},
        422: {"description": "Card failed schema validation."},
    },
)
async def validate_card(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Validate an existing Decision Card against the v0.1 schema (including
    conditional rules). Returns a summary on success; raises 422 on failure.
    """
    try:
        card = DecisionCard.model_validate(payload)
    except ValidationError as err:
        # Pydantic v2 includes the original Python exception object in ctx,
        # which isn't JSON-serialisable. include_context=False strips it.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "valid": False,
                "errors": err.errors(include_url=False, include_context=False),
            },
        ) from err

    return {
        "valid": True,
        "decision_id": card.decision_id,
        "status": card.decision.status,
        "buyer": card.buyer.name,
        "buyer_type": card.buyer.type,
        "vendor": card.subject.vendor_name,
        "product": card.subject.product_name,
        "documents_reviewed": len(card.subject.documents_reviewed or []),
        "conditions_count": len(card.conditions or []),
        "is_public": (card.publication.is_public if card.publication else False) or False,
    }
