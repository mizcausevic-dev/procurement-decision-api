# procurement-decision-api

[![CI](https://github.com/mizcausevic-dev/procurement-decision-api/actions/workflows/ci.yml/badge.svg)](https://github.com/mizcausevic-dev/procurement-decision-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Framework: FastAPI](https://img.shields.io/badge/framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)

> A local-first drafting aid for buyer-side AI procurement records. A buyer must review, authorize, and sign any decision before publication.

A FastAPI service that takes a buyer's own rubric judgments and fetches selected vendor [Kinetic Gain Protocol Suite](https://suite.kineticgain.com/) declarations, then returns a draft [AI Procurement Decision Card](https://github.com/mizcausevic-dev/ai-procurement-decision-spec). Package v0.2.0 implements the v0.1 card model; the upstream specification also has v0.2 and v0.3 fields that this service does not accept. The [NIST AI RMF crosswalk](https://suite.kineticgain.com/docs/nist-rmf-crosswalk.md) is informational, not a compliance determination or publication requirement.

## The cross-ecosystem bridge

This service connects vendor Suite declarations to a buyer-authored Decision Card:

```
Vendor publishes:                Buyer drafts (this service produces):
─────────────────────────        ────────────────────────────────────────
AEO Protocol Card           ┐
Tool Disclosure             │
Clinical AI Card            ├──> AI Procurement Decision Card
Student AI Disclosure       │       (status / rubric / conditions /
Agent Card                  │        fetched documents / rationale)
…the other six specs…       ┘
```

## Quick start

From this checkout, use the unreleased v0.2.0 code. The PyPI v0.1.1 package has the earlier API behavior.

```bash
python -m pip install -e .
export API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FETCH_ALLOWED_HOSTS="acmetutor.example"  # replace with an exact trusted vendor DNS name
procurement-decision-api  # listens on http://127.0.0.1:8088
```

PowerShell setup from this checkout:

```powershell
py -3.11 -m pip install -e .
$env:API_TOKEN = [Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:FETCH_ALLOWED_HOSTS = "acmetutor.example"
py -3.11 -m procurement_decision_api
```

Or via Docker:

```bash
docker build -t procurement-decision-api:local .
docker run --rm -p 127.0.0.1:8088:8088 -e API_TOKEN -e FETCH_ALLOWED_HOSTS procurement-decision-api:local
```

The example below uses fictional people, organizations, and URLs. Replace the fetch host with a trusted real vendor host before use. A fetched declaration is only a reference, not evidence that its claims are true. Draft and validation endpoints require `Authorization: Bearer <API_TOKEN>`.

Then draft a record:

```bash
curl -s http://localhost:8088/decisions/draft \
  -H "Authorization: Bearer $API_TOKEN" \
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
- `draft` — a v0.1 Decision Card draft. With no `proposed_status`, `decision.status` stays `pending`. The service does not sign or publish it.
- `documents_fetched[]` — each vendor URL with its retrieval timestamp + sha256 content hash
- `fetch_errors[]` — per-target retrieval errors (the draft doesn't fail wholesale on one missing URL)
- `suggested_status` — an advisory status inferred from the buyer-supplied rubric; it does not authorize approval

## What the service does

1. **Fetches** up to 16 operator-allowlisted HTTPS `/.well-known/*.json` URLs, without redirects, encoded paths, or query strings, concurrently with httpx. Each response is streamed with a 2 MB cap and 10 s total timeout. Missing `FETCH_ALLOWED_HOSTS` rejects requests with fetch targets.
2. **Suggests** a status from the buyer-supplied rubric if you didn't supply `proposed_status`; the draft itself remains `pending`. The suggestion rules:
   - Any `fail` → `rejected-with-remediation`
   - Any `partial` or `pass-with-condition` → `approved-with-conditions`
   - All `pass` → `approved`
   - Empty / all `n/a` → `pending`
3. **Composes** a default rationale from the buyer-supplied rubric results if you didn't supply `rationale_template`. It does not evaluate claims in fetched documents.
4. **Validates** the Decision Card against the same conditional rules the upstream zod schema enforces:
   - `status` ∈ {`approved-with-conditions`, `rejected-with-remediation`} → `conditions` must be non-empty
   - `status` = `withdrawn` → `withdrawal` block required
   - `publication.is_public` = `true` → `publication_uri` required
5. **Returns** the draft for human review. It does not create review-completed history events, sign, or publish. A draft request that claims `publication.is_public=true` is rejected.

## Endpoints

| Method | Path                       | Purpose |
|--------|----------------------------|---------|
| GET    | `/`                        | Service info + relevant links |
| GET    | `/healthz`                 | Liveness probe (always 200 if the process is running) |
| POST   | `/decisions/draft`         | Produce a Draft Decision Card |
| POST   | `/decisions/validate`      | Check an existing Decision Card against this service's v0.1 model |
| GET    | `/docs`                    | Interactive OpenAPI documentation (Swagger UI) |
| GET    | `/openapi.json`            | Machine-readable API schema |

## Why this matters

The service offers buyers a structured way to record their own AI procurement review and cite the vendor declarations they considered. Whether a buyer may or must publish a decision depends on its governing law, policy, contracts, and review process. [NIST describes the AI RMF as voluntary](https://www.nist.gov/itl/ai-risk-management-framework). OMB [M-25-21](https://www.whitehouse.gov/wp-content/uploads/2025/02/M-25-21-Accelerating-Federal-Use-of-AI-through-Innovation-Governance-and-Public-Trust.pdf) and [M-25-22](https://www.whitehouse.gov/wp-content/uploads/2025/02/M-25-22-Driving-Efficient-Acquisition-of-Artificial-Intelligence-in-Government.pdf) address covered federal executive agencies; they do not turn the fictional school-district example into a federal publication obligation.

The AI Procurement Decision Card spec defines a machine-readable format for buyer decisions. Here, a reviewer enters the rubric judgments and points the service at vendor declarations. The result still needs buyer review, a decision-authority check, and any required privacy or legal review before signing or publication.

The draft cites fetched declarations by URL, retrieval time, and content hash. Hashes can help detect later content changes only when producer and verifier use the same canonicalization contract. This package retains the published Python v0.1.1 hash algorithm for compatibility; it is not interoperable with the Rust attestation tool for all JSON values, including non-ASCII strings and exponent-form numbers. Do not use these hashes as cross-language proof of origin or unchanged content until a shared, versioned canonicalization rule is implemented and tested.

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

Pydantic v2 models implement the v0.1 fields and three conditional rules. They are not a substitute for validation against the current upstream JSON Schema. The `/decisions/validate` endpoint accepts v0.1 cards only.

## Operating boundary

- `API_TOKEN` is required for both POST endpoints and must contain at least 32 characters. Keep it in a secret store, rotate it through your deployment process, and send it only over TLS outside localhost. The CLI binds to `127.0.0.1` by default; the container listens inside its network namespace, so publish its port to localhost or place it behind a private, authenticated gateway.
- `FETCH_ALLOWED_HOSTS` is a comma-separated list of exact DNS names controlled by the operator. Only HTTPS port 443, unencoded `/.well-known/*.json` paths without credentials, queries, fragments, or redirects are fetched. This is an operator trust decision. DNS rebinding and a trusted host resolving to a private address remain possible; production egress policy must block private and metadata destinations.
- Fetched JSON is treated as untrusted data. The service does not validate a vendor declaration against its own Suite schema, verify a signature, or confirm the vendor's claims. Per-target failures are returned; the buyer must decide whether missing evidence changes the outcome.
- `AUDIT_STREAM_URL` is optional. When enabled, the emitted event contains status and counts only; it omits buyer/vendor names and decision IDs. The operator must assess the audit destination's access, retention, and deletion policy. `/docs` and `/openapi.json` remain public metadata endpoints.
- This package has no application-level rate limit, request-body cap, tenant authorization, or reviewer/signing workflow. The Docker base and runtime dependencies are not pinned to immutable versions. These are release gates for an internet-facing or multi-tenant deployment. Keep the service private until those controls and image provenance are verified.

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

- **Input documents** can be fetched from operator-allowlisted vendor `/.well-known/` paths. Validate each declaration against its own specification before relying on it; [`kg-validate-action`](https://github.com/mizcausevic-dev/kg-validate-action) is one available tool.
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
