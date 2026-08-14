# Browser Companion Capture Contract

## 1. Scope / Trigger

Apply this contract whenever code changes browser-companion pairing, extension authorization, `capture.v1`, canonical transcript storage, `ntu_kaltura`, extension CORS, or the captured-item worker path. The extension is an optional acquisition client. It coexists with `YouTubeConnector`; do not implement a global enable/disable flag that hides either path.

## 2. Signatures

Canonical HTTP and storage signatures:

```text
POST   /api/v1/browser-companion/extension/pairings
GET    /api/v1/browser-companion/extension/pairings/{pairing_id}
POST   /api/v1/browser-companion/extension/pairings/{pairing_id}:exchange
DELETE /api/v1/browser-companion/extension/grant
POST   /api/v1/browser-companion/extension/captures

POST   /api/v1/browser-companion/pairings/{pairing_id}:approve
GET    /api/v1/browser-companion/devices
DELETE /api/v1/browser-companion/devices/{device_id}

ContentItem.raw_format: "json3" | "capture_v1"
BrowserCompanionPairing -> BrowserCompanionGrant -> BrowserCapture -> IngestDispatch
```

`app/browser_capture.py` owns `BrowserCaptureRequest`, platform canonicalization, `capture-transcript.v1`, cue hashing, parsing, and timestamp links. `app/browser_companion.py` owns pairing/grant state. `app/browser_capture_submission.py` owns quota/idempotency/object staging and dispatch admission.

## 3. Contracts

- `capture.v1` contains `protocol_version`, bounded `client_version`, `platform`, validated `platform_id`, secret-free canonical/page URLs, bounded public metadata, caption status/source/language/cues, and the server-defined cue hash.
- Platforms are `youtube` and `ntu_kaltura`. Browser capture never sends a signed caption/playback URL for later server fetch.
- `caption.status=unavailable` requires null source/language and empty cues. It stores no transcript/media object and the worker produces `needs_asr`.
- Extension requests require an exact `chrome-extension://<32 a-p characters>` Origin. Configuration is `BROWSER_COMPANION_ALLOWED_ORIGINS`, `BROWSER_COMPANION_PAIRING_TTL_SECONDS`, `BROWSER_COMPANION_GRANT_TTL_SECONDS`, and `BROWSER_COMPANION_MAX_REQUEST_BYTES`.
- Pairing challenges and Bearer tokens are hash-only at rest. A grant has only `capture:write`; it cannot authenticate Web, conversation, management, or MCP routes. Web cookies cannot replace the extension Bearer.
- Same-origin approval/device mutation stays behind existing Web session, exact-Origin, and CSRF validation. Extension disconnect revokes its own token before local deletion; account-device revoke is tenant-scoped.
- Canonical transcript object keys are tenant-prefixed. Celery receives only the dispatch ID; no cue text, third-party token, signed URL, or browser credential enters PostgreSQL rows, broker messages, or logs.
- Existing `raw_format=json3` objects remain readable. New captured transcripts use `capture_v1` and the same bounded chunk/embed/completion path without a remote platform connector call.

## 4. Validation & Error Matrix

| Condition | Stable behavior |
| --- | --- |
| wrong/missing extension Origin | `extension_origin_invalid` / 403 before route execution |
| missing, expired, revoked, disabled, or wrong-scope grant | safe `extension_*` 401; Web/MCP sessions unchanged |
| unsupported `protocol_version` | `capture_protocol_unsupported` / 422 |
| unknown field, invalid ID/host/timing, signed cover URL | safe validation/capture error; no side effect |
| cue hash mismatch | `capture_content_hash_mismatch` |
| request/raw/cue/text limit exceeded | 413 or `capture_too_large`; no embedding |
| same tenant/idempotency key and same body | return the existing public result |
| same key and different body | `capture_conflict` |
| object put failure | cleanup best effort, capture/dispatch/item fail safely |
| broker publish failure | `queue_unavailable`; a new user action may retry the deterministic object |
| caption unavailable | `needs_asr`, no audio/video upload |

## 5. Good / Base / Bad Cases

- Good: a paired user reads a YouTube caption in the active browser, uploads normalized cues, and the worker reaches `ready` without calling `YouTubeConnector`.
- Good: an authorized Kaltura iframe consumes its page-scoped caption URL locally, returns only cues and a secret-free `ntu_kaltura` reference, and later citations use the platform link builder.
- Base: no extension is installed or paired. Existing server YouTube submission and legacy JSON3 reads behave exactly as before.
- Base: no caption track exists. The item reaches `needs_asr`; no media bytes leave the browser.
- Bad: copying a Web cookie, MCP token, Kaltura KS, SAML response, signed URL, or page exception into the capture payload/log. The strict schema and transport split exist to prevent this.

## 6. Tests Required

- Contract tests: unknown fields, untrusted hosts, platform IDs, finite ordered timings, cue/text/byte limits, cue hash, signed cover URL, legacy JSON3, and `capture_v1` parsing.
- Pairing tests: challenge/verifier, explicit approval, cross-tenant claim, single exchange, expiry, disabled user, hash-at-rest, resolve, self-revoke, account revoke, and replay.
- API tests: exact extension CORS, preflight methods/headers, capture Bearer vs Web cookie, Web CSRF, protocol-version error, public-only responses, and request ceiling.
- Submission/worker tests: quota/idempotency, object and broker compensation, available-to-ready, unavailable-to-`needs_asr`, no connector call, no duplicate object put, completion event, and deletion race.
- Migration checks: model/DDL parity, forward-compatible `ntu_kaltura`, `raw_format` default, downgrade retaining the enum value, and exactly one Alembic head.
- Extension gates: strict TypeScript, lint, fixture adapter tests, build, exact permission audit, no source maps/secrets, unpacked YouTube and NTULearn/Kaltura smoke.

## 7. Wrong vs Correct

### Wrong

```python
# A signed browser URL or Web credential must not be queued for server fetch.
publish({"url": caption_url, "cookie": request.cookies["session"]})
```

### Correct

```python
scope = companion.resolve_grant(capture_bearer)
result = capture_submission.submit(scope, validated_capture, request_key=key)
# The submission service persists/publishes the internal dispatch; the public
# result exposes only capture/item public IDs, platform, status, and lifecycle.
```

The extension consumes third-party authorization locally, the API revalidates normalized content, and the existing worker owns durable ingestion.
