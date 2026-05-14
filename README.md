# procurement-decision-api

[![CI](https://github.com/mizcausevic-dev/procurement-decision-api/actions/workflows/ci.yml/badge.svg)](https://github.com/mizcausevic-dev/procurement-decision-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Framework: FastAPI](https://img.shields.io/badge/framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)

> The machine that produces buyer-side AI procurement decisions, schema-conformant and ready to publish.

A FastAPI service that ingests a buyer's evaluation rubric plus a set of vendor [Kinetic Gain Protocol Suite](https://suite.kineticgain.com/) declarations and returns a draft [AI Procurement Decision Card](https://github.com/mizcausevic-dev/ai-procurement-decision-spec) (spec #11 of the Suite). The Decision Card is the canonical machine-readable carrier for NIST AI RMF-aligned procurement outcomes under OMB M-24-10 — see the [crosswalk doc](https://suite.kineticgain.com/docs/nist-rmf-crosswalk.md).

## The cross-ecosystem bridge

This is the first repo that **composes** the [Kinetic Gain Protocol Suite](https://suite.kineticgain.com/) with the [Decision Intelligence Engines](https://github.com/mizcausevic-dev?tab=repositories) portfolio:

```
Vendor publishes:                Buyer publishes (this service produces):
─────────────────────────        ────────────────────────────────────────
AEO Protocol Card           ┐
Tool Disclosure             │
Clinical AI Card            ├──> AI Procurement Decision Card
Student AI Disclosure       │       (status / rubric / conditions /
Agent Card                  │        documents reviewed / rationale)
…the other six specs…       ┘
```

## Quick start

```bash
pip install procurement-decision-api
procurement-decision-api  # listens on http://0.0.0.0:8088
```

Or via Docker:

```bash
docker run -p 8088:8088 ghcr.io/mizcausevic-dev/procurement-decision-api:latest
```

Then draft a decision:

```bash
curl -s http://localhost:8088/decisions/draft \
  -H 'content-type: application/json' \
  -d '{
    "decision_id": "SPRINGFIELD-DEC-2026-001",
    "buyer": {
      "name": "Springfield Unified School District",
      "type": "school-district",
      "jurisdiction": "US-CA"
    },
    "decision_maker": {
      "role": "Director of Educational Technology",
      "name": "Dr. Jane Doe",
      "authority": "Board Resolution 2026-04"
    },
    "vendor_name": "AcmeTutor Inc.",
    "product_name": "AcmeTutor 3.0",
    "vendor_id": "https://acmetutor.example/.well-known/aeo.json",
    "fetch_targets": [
      { "type": "aeo",                    "url": "https://acmetutor.example/.well-known/aeo.json" },
      { "type": "tutor-card",             "url": "https://acmetutor.example/.well-known/tutor-card.json" },
      { "type": "student-ai-disclosure",  "url": "https://acmetutor.example/.well-known/student-ai-disclosure.json" }
    ],
    "policy_uris": [
      "https://springfield.edu/.well-known/aup.json"
    ],
    "rubric": [
      { "id": "ferpa-compliance",         "result": "pass", "weight": 1.0 },
      { "id": "coppa-compliance",         "result": "pass", "weight": 1.0 },
      { "id": "no-training-on-student-data", "result": "pass-with-condition", "weight": 1.0,
        "notes": "Disclosure asserts no-training; require contractual confirmation." },
      { "id": "bias-audit-completed",     "result": "partial", "weight": 0.8,
        "notes": "Audit current but due for refresh by 2026-09." }
    ],
    "conditions": [
      { "id": "no-training-restriction",
        "description": "Vendor SHALL NOT use Springfield USD student-provided content for model training.",
        "enforcement": "contractual" },
      { "id": "bias-audit-refresh",
        "description": "Vendor SHALL deliver a refreshed third-party bias audit by 2026-12-01.",
        "enforcement": "audit" }
    ]
  }' | jq
```

The response includes:
- `draft` — the full, schema-conformant Decision Card (ready to sign + publish at `/.well-known/decisions/<id>.json`)
- `documents_fetched[]` — each vendor URL with its retrieval timestamp + sha256 content hash
- `fetch_errors[]` — per-target retrieval errors (the draft doesn't fail wholesale on one missing URL)
- `inferred_status` — `true` if the service inferred the decision status from the rubric

## What the service does

1. **Fetches** every URL in `fetch_targets` concurrently with httpx, capped at 2 MB / 10 s per document, and computes a canonical sha256 hash over each (sorted keys, no whitespace).
2. **Infers** `decision.status` from the rubric if you didn't supply `proposed_status`. The inference rules:
   - Any `fail` → `rejected-with-remediation`
   - Any `partial` or `pass-with-condition` → `approved-with-conditions`
   - All `pass` → `approved`
   - Empty / all `n/a` → `pending`
3. **Composes** a default rationale from the rubric results if you didn't supply `rationale_template`.
4. **Validates** the Decision Card against the same conditional rules the upstream zod schema enforces:
   - `status` ∈ {`approved-with-conditions`, `rejected-with-remediation`} → `conditions` must be non-empty
   - `status` = `withdrawn` → `withdrawal` block required
   - `publication.is_public` = `true` → `publication_uri` required
5. **Returns** the Draft Decision Card. Review, edit, sign, publish.

## Endpoints

| Method | Path                       | Purpose |
|--------|----------------------------|---------|
| GET    | `/`                        | Service info + relevant links |
| GET    | `/healthz`                 | Liveness probe (always 200 if the process is running) |
| POST   | `/decisions/draft`         | Produce a Draft Decision Card |
| POST   | `/decisions/validate`      | Validate an existing Decision Card against the v0.1 schema |
| GET    | `/docs`                    | Interactive OpenAPI documentation (Swagger UI) |
| GET    | `/openapi.json`            | Machine-readable API schema |

## Why this matters

AI procurement under OMB M-24-10 and NIST AI RMF requires agencies to publish reviewable decisions about vendor AI systems. Today, those decisions sit in PDFs and procurement databases — invisible to vendors trying to win future RFPs and invisible to citizens whose data is being processed.

The AI Procurement Decision Card spec defines a machine-readable carrier for those decisions. This service is the tool that produces them at scale: a reviewer fills in the rubric, points at the vendor's published declarations, and gets back a schema-valid card ready to publish at `/.well-known/decisions/<decision_id>.json`.

For procurement teams, this means a decision becomes a queryable, searchable, audit-friendly artifact — and the vendor's published declarations are cited by URL and content hash, so any drift after the decision is detectable.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                  FastAPI app (lifespan-managed)            │
│                                                            │
│   POST /decisions/draft                                    │
│       │                                                    │
│       ▼                                                    │
│   ┌────────────────────────────────────────────────┐       │
│   │ fetcher.fetch_documents (async, httpx)         │       │
│   │   - timeout 10s per doc                        │       │
│   │   - 2 MB size cap                              │       │
│   │   - canonical sha256 hash                      │       │
│   │   - per-target error collection                │       │
│   └────────────────────────────────────────────────┘       │
│       │                                                    │
│       ▼                                                    │
│   ┌────────────────────────────────────────────────┐       │
│   │ rubric.infer_status                            │       │
│   │ rubric.compose_rationale                       │       │
│   │ rubric.weighted_score                          │       │
│   └────────────────────────────────────────────────┘       │
│       │                                                    │
│       ▼                                                    │
│   ┌────────────────────────────────────────────────┐       │
│   │ drafter.draft_decision_card                    │       │
│   │   - validates conditional rules                │       │
│   │   - assembles history events                   │       │
│   └────────────────────────────────────────────────┘       │
│       │                                                    │
│       ▼                                                    │
│   DraftResponse                                            │
└────────────────────────────────────────────────────────────┘
```

Pydantic v2 models mirror the JSON Schema 2020-12 spec exactly, including the conditional rules (which run as `@model_validator(mode="after")` hooks).

## Development

```bash
git clone https://github.com/mizcausevic-dev/procurement-decision-api
cd procurement-decision-api
pip install -e ".[dev]"

# Run the test suite (mocks the vendor HTTP layer; no internet required)
pytest -q

# Lint, format, typecheck
ruff check src tests
ruff format src tests
mypy src

# Run the service
python -m procurement_decision_api
# or
uvicorn procurement_decision_api.app:app --reload --port 8088
```

## Composability

This service composes naturally with the rest of the Kinetic Gain ecosystem:

- **Input documents** can be fetched directly from any vendor's `/.well-known/` paths, or validated first via [`kg-validate-action`](https://github.com/mizcausevic-dev/kg-validate-action) in your CI.
- **Output Decision Cards** can be inspected by [`mcp-kinetic-gain`](https://github.com/mizcausevic-dev/mcp-kinetic-gain) (tools: `decision_card_inspect`, `decision_card_validate`).
- **Inline validation** in the browser is available at [validator.kineticgain.com](https://validator.kineticgain.com/) — paste the produced draft, get inline error markers.

## License

MIT. The Kinetic Gain Protocol Suite specifications this service produces are also MIT; reference implementations like [`mcp-kinetic-gain`](https://github.com/mizcausevic-dev/mcp-kinetic-gain) are AGPL-3.0.

## Related

- **Spec repo:** [`ai-procurement-decision-spec`](https://github.com/mizcausevic-dev/ai-procurement-decision-spec)
- **Hosted validator:** [validator.kineticgain.com](https://validator.kineticgain.com/)
- **MCP server:** [`mcp-kinetic-gain`](https://github.com/mizcausevic-dev/mcp-kinetic-gain) — install with `npx -y mcp-kinetic-gain`
- **GitHub Action:** [`kg-validate-action`](https://github.com/mizcausevic-dev/kg-validate-action)
- **NIST AI RMF crosswalk:** [suite.kineticgain.com/docs/nist-rmf-crosswalk.md](https://suite.kineticgain.com/docs/nist-rmf-crosswalk.md)
- **Apex:** [kineticgain.com](https://kineticgain.com/)
