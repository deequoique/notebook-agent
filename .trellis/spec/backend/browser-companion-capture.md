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
- State-changing extension requests require an exact `chrome-extension://<32 a-p characters>` Origin. Chrome MV3 may omit Origin on the read-only pairing-status GET, so only `GET /api/v1/browser-companion/extension/pairings/{32-hex-public-id}` may proceed without Origin. A development-only `chrome-extension://*` configuration is valid only when the Web server is bound to loopback; production continues to require exact IDs. Configuration is `BROWSER_COMPANION_ALLOWED_ORIGINS`, `BROWSER_COMPANION_PAIRING_TTL_SECONDS`, `BROWSER_COMPANION_GRANT_TTL_SECONDS`, and `BROWSER_COMPANION_MAX_REQUEST_BYTES`.
- Pairing challenges and Bearer tokens are hash-only at rest. A grant has only `capture:write`; it cannot authenticate Web, conversation, management, or MCP routes. Web cookies cannot replace the extension Bearer.
- Same-origin approval/device mutation stays behind existing Web session, exact-Origin, and CSRF validation. Extension disconnect revokes its own token before local deletion; account-device revoke is tenant-scoped.
- Canonical transcript object keys are tenant-prefixed. Celery receives only the dispatch ID; no cue text, third-party token, signed URL, or browser credential enters PostgreSQL rows, broker messages, or logs.
- The browser-capture publish budget starts immediately before broker I/O. Remote database admission and object staging do not consume that budget; `queue_unavailable` represents a bounded publisher failure, not slow PostgreSQL work completed before publication.
- Publisher acknowledgements lock and re-check a dispatch before changing `pending` to `enqueued`/`failed`; they must never overwrite a faster worker's `running` or `completed` state. The same rule applies to ordinary ingestion retries and browser-capture admission.
- NTULearn capture may enter the exact user-invoked `https://ntulearnv1.ntu.edu.sg/*` media page and `https://cdnapisec.kaltura.com/*` player host to read native text tracks or fetch a page-authorized caption asset locally. Prefer the concrete player frame over a generic Media Gallery shell, and never return the caption asset URL, KS, query signature, cookie, or authorization material from the injected adapter.
- Kaltura `caption_captionasset/action/serveWebVTT/.../*.m3u8` resources are VOD WebVTT playlists: the extension may resolve only bounded, trusted relative timed-text segments, apply each segment's timestamp map, and merge normalized cues locally. CSS/JS pseudo-resources, untrusted fragments, partial segment reads, oversized bodies, and stalled requests fail safely without entering `capture.v1`.
- YouTube caption tracks may expose a signed `/api/timedtext` endpoint whose JSON3 representation is empty or non-JSON even while the player renders captions. After validating the original trusted HTTPS host/path, try JSON3 first, then WebVTT and the unmodified original XML representation. Revalidate every response redirect and never return or log the signed URL/query.
- If every trusted timed-text representation is empty, the page's official same-origin `youtubei/v1/get_transcript` endpoint may be used as a final fallback. Base64url transcript params must decode to the exact current video ID before the request; accept only an exact same-origin endpoint response and project only finite ordered segment cues. Never return or log the public client key, InnerTube context, opaque params, response body or browser credential.
- Existing `raw_format=json3` objects remain readable. New captured transcripts use `capture_v1` and the same bounded chunk/embed/completion path without a remote platform connector call.
- A retry of a captured item reuses the latest tenant-matching `ready` `BrowserCapture` for that item even though the capture remains linked to the original dispatch attempt. It must not require that original dispatch to remain `running`, and it must never reuse another tenant's capture.
- Captured transcripts above 512 cues skip per-cue semantic-boundary embedding and use deterministic hard cuts before embedding final searchable chunks. This bounds provider work for multi-hour recordings while preserving the 120-second hard segment boundary.
- A packaged extension selects exactly one Notebook Agent API origin through its exact manifest host permission. The production artifact allows only `https://notebookai.deequoique.tech/*`; a local qualification artifact allows only loopback `http://127.0.0.1:8000/*`, while the authenticated approval UI remains on `https://localhost:8443`. Never ship both API origins in one manifest, and audit each build target independently. Loopback HTTP avoids relying on browser exceptions for an untrusted local HTTPS issuer without widening access beyond the machine.
- Extension API fetches are bounded to 10 seconds and map timeout/network failures to stable safe popup errors. A missing, nearby, or ambiguous API host permission fails closed before pairing or capture; the popup must never remain indefinitely busy on an unreachable deployment.
- Persisted extension pairing/grant state is bound to the selected API origin. Switching between production and local artifacts clears cross-target credentials before any request; legacy state without an origin may be adopted only by the original production target.

## 4. Validation & Error Matrix

| Condition | Stable behavior |
| --- | --- |
| wrong/missing Origin on state-changing extension route | `extension_origin_invalid` / 403 before route execution |
| missing Origin on exact read-only MV3 pairing-status GET | route executes; no CORS allow-origin header is invented |
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
- API tests: exact extension CORS, the narrow missing-Origin MV3 status exception, missing-Origin rejection on every extension mutation, preflight methods/headers, capture Bearer vs Web cookie, Web CSRF, protocol-version error, public-only responses, and request ceiling.
- Submission/worker tests: quota/idempotency, object and broker compensation, available-to-ready, unavailable-to-`needs_asr`, no connector call, no duplicate object put, completion event, and deletion race.
- Migration checks: model/DDL parity, forward-compatible `ntu_kaltura`, `raw_format` default, downgrade retaining the enum value, and exactly one Alembic head.
- Extension gates: strict TypeScript, lint, fixture adapter tests, production and local builds, per-target exact permission audits, API-origin/timeout tests, no source maps/secrets, unpacked YouTube and NTULearn/Kaltura smoke.

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
