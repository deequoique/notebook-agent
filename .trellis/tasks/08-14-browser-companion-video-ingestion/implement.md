# Browser companion video ingestion implementation plan

## Preconditions

- Keep task status `planning` until the user approves the latest final planning summary.
- Preserve the existing server-side YouTube connector and loopback proxy behavior throughout the pilot.
- Treat the extension as an optional user-installed acquisition method. Do not add a global default-off product switch; access is controlled by install/pair, credential scope, disconnect/revoke, browser disablement, and uninstall.
- Work in small commits/review gates; do not mix unrelated untracked task work.

## Phase 1 — Contracts, fixtures, and security skeleton

- [ ] Add sanitized YouTube and NTULearn/Kaltura metadata/caption fixtures under test ownership. Prove no cookie, SAML, KS, authorization header, or signed-query secret is retained.
- [x] Define `capture.v1`, pairing, device, and result Pydantic schemas with strict unknown-field rejection, bounded strings/collections, stable safe errors, and OpenAPI coverage.
- [x] Define one canonical `capture-transcript.v1` raw schema/parser that yields existing `Cue`/metadata structures; keep legacy JSON3 parsing intact.
- [x] Add centralized platform canonicalization and timestamp-link interfaces for YouTube and `ntu_kaltura`.
- [x] Add configuration for pairing/grant TTLs, exact allowed extension origins, and request-body limits; validate unsafe/wildcard values at startup.
- [x] Permit `chrome-extension://*` only for a loopback-bound development Web server while preserving exact production Origin validation.
- [x] Review gate: threat-model page-world messages, pairing theft/replay, token storage, CORS, body decompression, signed-URL leakage, tenant confusion, and idempotency conflicts before migrations.

## Phase 2 — Database and extension authorization

- [x] Add SQLAlchemy models and one Alembic revision for browser companion pairings, grants, and captures, including ownership, uniqueness, expiry/revocation, cleanup intent, and state constraints.
- [x] Add service-level pairing create/approve/exchange/list/revoke behavior with PostgreSQL locking, single-use verifier exchange, token hashing, bounded rate limits, and privacy-safe errors.
- [x] Add a dedicated Bearer resolver that returns `UserScope` and the capture scope only; do not alter Web cookie or MCP credential dependencies.
- [x] Add same-origin CSRF-protected Web approval/list/revoke routes and exact-extension-origin exchange/status routes.
- [ ] Test cross-tenant, replay, expiry, duplicate approval/exchange, token rotation/revocation, disabled account, malformed origin, and credential-isolation cases.
- [ ] Run migration single-head/model-constraint checks and upgrade/downgrade roundtrip on an isolated database.
- [x] Review point: only an explicitly paired extension grant can use capture routes; unpaired users and existing Web/MCP clients retain their current behavior.

## Phase 3 — Durable capture admission and ingestion reuse

- [ ] Generalize URL/platform registration so YouTube and NTU Kaltura canonical references use one validation boundary without regressing batch URL save.
- [x] Add the capture endpoint with Bearer scope, exact CORS policy, request/body/decompressed ceilings, idempotency, protocol-version negotiation, metadata/cue normalization, content hashing, and existing tenant/global quotas.
- [x] Extend the submission service to create/restore/deduplicate items and capture dispatches under existing tenant locks. Preserve public result semantics and broker compensation.
- [ ] Persist deterministic raw-object cleanup intent before object I/O, store canonical transcript JSON, and add retry/repair behavior for crash windows without holding a database transaction across storage or broker calls.
- [x] Route captured dispatches through the existing worker: bounded read, revalidation, metadata application, `needs_asr` for caption-unavailable, chunking, embedding, completion outbox, and deletion-race handling. Never call a remote connector for a ready capture.
- [x] Make transcript reading format-aware and preserve existing tenant-prefix, size, cursor, and legacy JSON3 behavior.
- [ ] Test malformed/oversized/timing-invalid payloads, body-hash conflict, duplicate submit, quota exhaustion, object-store and broker failures, repair, deletion/restore races, capture-to-ready, capture-to-needs_asr, and zero transcript bytes in broker payloads. (Broker-budget isolation from slow database admission is covered.)
- [x] Review point: without an installed/paired extension, all pre-existing ingestion and transcript paths behave identically.

## Phase 4 — Platform-aware backend and Web product

- [x] Add `ntu_kaltura` to models/migrations/DTOs and audit every exhaustive platform branch, database constraint, test fixture, lifecycle projection, source label, link builder, and CSP rule.
- [x] Centralize timestamp/source projections for transcript blocks and Agent citations; verify YouTube timestamps and Kaltura canonical/deep links.
- [x] Update capabilities, Add Videos, item details, lifecycle/action copy, and `youtube_rate_limited` recovery to direct paired users to browser capture.
- [x] Add the same-origin pairing approval and paired-device/revocation UI using generated OpenAPI types and existing query/client/session rules.
- [x] Preserve a validated pending approval destination across Web login; make public entry points download the extension; distinguish approval from completed connection and expose actionable pairing errors.
- [x] Preserve distinct loading, error, unsupported, permission, unpaired, processing, `needs_asr`, and successful states with concise Chinese copy and accessible mobile controls.
- [ ] Regenerate OpenAPI JSON and TypeScript together; test CSP, no private IDs, CSRF, cache teardown, server-derived actions, and 390×844 interaction. (Contracts/tests complete; real 390×844 browser smoke remains.)

## Phase 5 — Manifest V3 extension

- [x] Scaffold `extension/` with a pinned pnpm toolchain, strict TypeScript, ESLint, Vitest, deterministic build/package scripts, and a Manifest V3 manifest using minimum/exact permissions.
- [x] Implement service-worker ownership of pairing verifier/Bearer, local-only storage, disconnect, capture idempotency, submission/status, safe retries, and version negotiation.
- [ ] Implement a typed adapter dispatcher and nonce-bound page bridge with strict platform/host/message validation.
- [ ] Implement YouTube metadata/caption selection and cue normalization against sanitized fixtures; submit `unavailable` when no usable captions exist.
- [x] Implement NTULearn embedded/direct Kaltura discovery and supported caption parsing against sanitized fixtures; consume and discard page-scoped signed authorization locally.
- [x] Implement the single extension action/popup with permission, pairing, progress, result, `needs_asr`, adapter-change, and recovery states.
- [x] Prove the packaged artifact contains no environment secrets, broad host wildcard, remote executable code, third-party session material, transcript fixtures, or source maps that expose secrets.
- [ ] Run an unpacked-extension smoke on controlled fixture pages before live canaries.

## Phase 6 — Full validation and pilot rollout

- [ ] Backend focused tests: pairing/grants, capture schemas/API/service, worker capture path, transcript formats, platform links, lifecycle/completion, tenant isolation, request ceilings, logs, and migration parity.
- [ ] Backend full suite: `pytest` plus repository deployment/static checks and exactly one Alembic head.
- [x] Web gates from `web/`: `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm build`, `pnpm check:api`.
- [ ] Extension gates from `extension/`: test, typecheck, lint, build, package-manifest/permission audit, and fixture-page browser smoke.
- [x] Security inspection: search built artifacts/log fixtures for sentinel cookies, SAML, KS, Bearer, signed URLs, transcript text, internal IDs, wildcard CORS, and remote code.
- [ ] Staging: pair, revoke, re-pair, save one caption-bearing YouTube fixture/live canary, submit one no-caption item to `needs_asr`, and verify duplicate capture returns the same item.
- [ ] Production pilot: exact extension build/ID, one public YouTube canary while server acquisition is `youtube_rate_limited`, then one user-authorized NTULearn/Kaltura caption video. Verify title, timings, transcript, citations, search, tenant ownership, completion notification, and safe metrics.
- [ ] Confirm disconnecting/revoking pairing and disabling/uninstalling the extension stop future browser captures while existing items remain readable and server-side YouTube still works.

## Risky files and review focus

- `app/models.py` and Alembic revisions: enum/check-constraint parity, tenant foreign keys, single head, rollback compatibility.
- `app/api/app.py` and auth dependencies: never weaken same-origin Web CSRF or Web/MCP credential isolation; no wildcard credentialed CORS.
- `app/ingest/submission.py` and `app/ingest/tasks.py`: quota locks, deduplication, broker compensation, object cleanup, deletion races, and no connector fetch for captured content.
- `app/web/transcript.py` and `app/agent/services.py`: legacy JSON3 compatibility and platform-aware timestamp correctness.
- Extension page bridge/adapters: untrusted page input, minimum permissions, signed-token leakage, fixture drift, and no remote code.

## Final implementation review gates

- [ ] Every PRD acceptance criterion maps to an automated test or named live pilot check.
- [ ] No blocking product decision or unresolved security boundary remains.
- [ ] Technical validations listed in `design.md` are recorded with sanitized evidence.
- [ ] Disconnect, grant revocation, browser disable/uninstall behavior, and release compatibility are tested before distribution.
- [ ] Any durable new convention discovered during implementation is proposed for `.trellis/spec/` before task completion.
