# Release hardening plan, 2026-10-07

## Goal and current state

Prepare the local 0.2.0 API branch for a release review. The prior review fixed draft status, URL allowlisting, outbound response limits, authentication, and release workflow checks. This continuation addresses remaining feasible service controls and records anything that still requires deployment evidence or an upstream schema change.

## Scope and acceptance

- Bound request bodies and authenticated request rate before expensive drafting work.
- Reject fetch hostnames that currently resolve to non-public IP addresses and disable environment proxy inheritance; keep the documented need for network egress policy because DNS can change after preflight.
- Keep draft authority explicit: a bearer token authorizes service use, not buyer approval or publication.
- Pin the container base and runtime dependency resolution enough to make a release image reviewable.
- Preserve the v0.1 Decision Card schema while coordinating versioned document hashes with the Rust signer.
- Run affected tests, lint, types, build/content checks, dependency and scoped secret checks. Commit locally only.

## Risks and decisions

- The current spec's `DocumentReference` rejects unknown fields, so a hash profile cannot be embedded there. Any new profile metadata must remain outside the card until the spec adds it.
- A DNS preflight is a defense in depth check, not a substitute for deployment egress controls.
- Process-local rate limiting is a backstop; a gateway must handle distributed traffic and unauthenticated floods.

## Sequence and verification

1. Implement body/rate limits and focused error-path tests.
2. Add DNS preflight and proxy isolation with mocked resolution tests.
3. Add versioned hash metadata only after agreeing on a cross-language vector.
4. Pin image/dependencies, inspect the package/image recipe and affected workflows.
5. Run the verification stack, review the full diff and rollback path, then commit.

## Deploy and rollback boundary

No push, publish, merge, or deployment from this worktree. A release operator must verify CI on the exact commit, image build and digest, gateway authentication/egress rules, and a rollback to the prior image/package before public traffic. Local rollback is a revert of this commit.

## Progress and outcome

Implemented locally on `codex/release-review-2026-10-07` from base `94911dc6297b69cbd17a6c91044742d988da6b81`:

- Every generated card stays pending; caller and rubric statuses are advisory. Structural validation no longer echoes buyer/vendor identifiers or reports an unsigned card as authority verified.
- Bounded request bodies and process-local authenticated rate; added DNS public-address preflight, no proxy environment inheritance, duplicate-key rejection, and per-target JCS-domain errors.
- Added `document_hashes[]` as an informational `jcs-rfc8785-v1` response field while preserving the Decision Card v0.1 legacy hash; tested against the Rust fixture. The card itself cannot carry hash-profile metadata under the upstream schema.
- Pinned the container base digest and GitHub Actions commits, added a universal dependency lock, frozen CI installs, a distribution-content check, and a Docker context allowlist.

Local verification is recorded in the final task report. Public release remains **BLOCKED** until the exact pushed commit passes remote CI, the Linux image builds and its digest is inspected, and the deployment supplies private/authenticated ingress, network egress restrictions, caller-specific authorization, an independent buyer review/signing process, and retention/deletion policies for clients and the optional audit destination. The shared service token is not reviewer authority. A DNS preflight can race a later connection.
