# Browser companion pairing failure investigation plan

## Phase 1 — Freeze and inventory

- [x] Record current branch, dirty files, running Web/worker/Beat sessions, local extension build directory, manifest version, API Origin, host permissions, and packaged download checksum.
- [x] Record latest pairing states and grant count without reading secret columns.
- [x] Stop making speculative security/configuration changes until one attempt is fully correlated.

## Phase 2 — Safe observability

- [x] Add safe rejected-Origin diagnostics with request ID and Origin only.
- [x] Confirm logs exclude verifier, challenge, token, cookie, authorization, transcript, internal user ID, and database URL.
- [x] Remove inaccurate version language from the Origin error.
- [x] Rebuild/redeploy and obtain one controlled pairing attempt.

## Phase 3 — Correlated reproduction

- [x] Capture pre-attempt pairing/grant snapshot.
- [x] Correlate create, approval, and rejected status events from fresh `0.1.2` attempts.
- [x] Capture the exact safe failure (`extension_origin_invalid`, `origin=<missing>`) and database post-state (`approved`, no grant).
- [x] Write the root-cause finding into `research/root-cause.md` before changing the functional boundary.

## Phase 4 — Minimal repair

- [x] Permit missing Origin only on the exact read-only pairing-status GET; retain Origin enforcement on every state-changing extension route.
- [x] Correct the popup's inaccurate version-mismatch copy.
- [x] Preserve PKCE, single-use exchange, TTL, least-privilege grant, production exact Origin, and secret-free logging.
- [x] Rebuild and redeploy locally after user approval.
- [x] Point the public Web download at an audited production package whose only Notebook Agent API host is `https://notebookai.deequoique.tech/*`; retain the loopback package for explicit local development only.

## Phase 5 — User-approved verification

- [ ] After approval, run focused service tests for the proven boundary.
- [ ] Run one live local pairing and confirm pairing `used`, exactly one grant, and a visible connected device.
- [ ] Run relevant frontend/extension checks, then the proportional full gates.
- [ ] Remove or reduce temporary diagnostics, update durable specs if needed, and hand off remaining risks.
