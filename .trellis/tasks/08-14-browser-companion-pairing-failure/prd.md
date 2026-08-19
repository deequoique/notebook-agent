# Browser companion pairing failure investigation

## Goal

Determine, with correlated evidence, why the local Chrome companion can create and approve fresh pairings but cannot exchange them for a device grant. Produce a minimal fix that makes the local pairing flow reliable without weakening the production trust boundary.

## Requirements

- Treat every browser screenshot and user action as evidence, not as proof of a guessed root cause.
- Correlate one fresh attempt across extension version/build directory, runtime extension ID and Origin, pairing public ID, API request ID, database state, and the final browser-safe error code.
- Distinguish create, status, approve, exchange, and device-list failures. Do not collapse them into a generic “version mismatch” diagnosis.
- Never read, log, upload, or expose the raw pairing verifier, grant token, cookies, CSRF values, SAML/Kaltura credentials, signed URLs, or transcript content.
- Preserve the PKCE verifier check, single-use exchange, ten-minute pairing TTL, revocable `capture:write` grant, and exact production Origin policy.
- Development-only wildcard Origin support must remain restricted to a loopback-bound development Web server.
- Account for intermittent SQLAlchemy `OperationalError` events against the remote development PostgreSQL database and determine whether they cause or only accompany the exchange failures.
- Keep the currently running no-Docker Web, worker, and Beat setup recoverable; do not require Docker.
- Do not run automated or live browser tests until the user reviews and approves this plan.

## Acceptance Criteria

- [ ] A single attempt has a redacted timeline showing extension build/version, extension Origin classification, pairing ID, create/approve/status/exchange HTTP outcomes, request IDs, and database `pending/approved/used` plus grant existence.
- [ ] The root cause is demonstrated by a reproducible failing boundary or a captured backend exception, not inferred from popup wording.
- [ ] Popup copy reports the actual safe error category and never recommends “upgrade” for an Origin mismatch unless the build is genuinely incompatible.
- [ ] Web approval says “approved” until exchange has created a grant; “connected” appears only when a device grant exists.
- [ ] A fresh local pairing reaches `used`, creates exactly one non-revoked device grant, and appears in the authenticated device list after the user approves live verification.
- [ ] Expired, invalid, used, Origin-rejected, network-unavailable, and database-unavailable cases each have distinct recovery guidance.
- [ ] Production continues to reject arbitrary extension Origins; the development wildcard cannot start on a production or non-loopback Web configuration.
- [ ] No diagnostic output contains verifier/token/session/transcript material.

## Notes

Known evidence as of 2026-08-14 Asia/Singapore:

- Pairings `6288158c…`, `e01c0c6b…`, and `1582cb56…` were newly created and approved, remained unconsumed, and produced zero grants.
- An arbitrary syntactically valid Chrome extension Origin received `200` after the development wildcard restart, proving that the wildcard branch can be active.
- The Web process later emitted repeated SQLAlchemy `OperationalError` events while direct `SELECT 1` and a subsequent read-only pairing-status request succeeded, indicating an intermittent or request-path-specific database failure.
- Existing popup wording has previously hidden distinct backend failures and therefore is not authoritative evidence of the failing boundary.
