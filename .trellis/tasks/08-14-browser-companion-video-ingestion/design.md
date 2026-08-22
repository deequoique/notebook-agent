# Browser companion video ingestion design

## Architecture and ownership

```text
YouTube or NTULearn/Kaltura page
  -> platform adapter in the MV3 extension
  -> normalized capture.v1 envelope (metadata + timed cues only)
  -> extension service worker with capture-only Bearer credential
  -> Notebook Agent capture API
  -> tenant-scoped submission/idempotency/quota service
  -> durable canonical transcript object + capture record + ingest dispatch
  -> existing worker validation/chunking/embedding/completion path
  -> platform-aware transcript/detail/citation UI
```

The extension is an acquisition client, not a second ingestion engine. It may transiently use the current page's authenticated context to discover and download captions, but it discards third-party request/session material before constructing the capture envelope. Notebook Agent remains responsible for identity, tenant boundaries, validation, deduplication, quotas, durable storage, retrieval, lifecycle, and completion events.

The current server-side `YouTubeConnector` remains available. Browser capture is the preferred recovery path after `youtube_rate_limited` and the only MVP path for authenticated NTULearn/Kaltura media.

## Trust boundaries

### Third-party page boundary

- Treat YouTube, Blackboard, Kaltura, and all page JavaScript as untrusted input.
- Use separate YouTube and Kaltura adapters behind one typed adapter interface. Page-world access is limited to extracting the minimum player metadata/caption information needed for the current user gesture.
- Where page-world execution is required, inject a small nonce-bound bridge. Return only allowlisted primitive metadata and caption text; reject unsolicited `postMessage` traffic, prototype-rich objects, credentials, headers, and URLs outside the expected platform hosts.
- Never use `webRequest` interception as a general traffic recorder and never record or upload cookies, authorization headers, SAML responses, Kaltura KS values, signed manifests, or signed caption URLs.

### Extension boundary

- Build a separate top-level `extension/` Manifest V3 package using TypeScript, pnpm, strict type checking, ESLint, and Vitest. The extension artifact is packaged independently from the server-hosted React SPA.
- The service worker owns Notebook Agent pairing, Bearer storage, capture submission, retry, and status. Page/content scripts never receive the long-lived Notebook Agent credential.
- Store the capture credential only in `chrome.storage.local`, never sync storage, page DOM, page local/session storage, a URL, logs, analytics, or error text.
- Request only the permissions needed for a user-invoked capture: `activeTab`, `scripting`, `storage`, exact supported video hosts, and the configured Notebook Agent origin. Use optional host permissions where practical and provide an actionable permission prompt.
- One popup/action presents `unsupported`, `permission_required`, `not_paired`, `ready`, `discovering`, `uploading`, `saved`, `needs_asr`, and safe failure states.

### Backend boundary

- Do not relax the current Web cookie/CSRF contract. Web-session cookies approve and revoke extension pairings only on the same-origin account surface.
- Add a dedicated extension-device grant model and resolver. Reuse the MCP grant security pattern—high-entropy secret, hash-at-rest, expiry, revocation, disablement, bounded scopes—but do not reuse MCP credentials or authenticate normal browser/MCP routes with extension tokens.
- Permit extension-origin requests only for the pairing exchange/status and capture API. Configure an exact allowlist of shipped extension origins/IDs and the configured API origin; never use wildcard credentialed CORS. Bearer capture calls do not send Web cookies.
- Capture grants expose only the capability needed to create/read the result of captures for their owning tenant. They do not grant library-wide read, conversation, management, MCP, or account access.

## Pairing protocol

Use an OAuth-device/PKCE-shaped flow without importing the Web session into the extension:

1. The extension generates a high-entropy verifier locally and derives a challenge.
2. It opens the same-origin Notebook Agent pairing page with only the public challenge and a non-secret client label/version.
3. The signed-in user explicitly approves the device through a CSRF-protected POST. If logged out, normal Notebook Agent login occurs first.
4. The extension exchanges the original verifier once for a capture-only Bearer credential through the extension API boundary.
5. The backend persists only hashes of verifier/token material, binds the grant to the approving `app_user_id`, and returns the raw credential exactly once.
6. The extension can disconnect locally; the Notebook Agent account page can list coarse device metadata and revoke server-side access. Expired/revoked credentials produce `extension_pairing_required` without invalidating the Web session.

Pairing requests are single-use, short-lived, rate-limited, and consume atomically. Raw token values, internal user/session IDs, and third-party identities never appear in DTOs. Exact TTL and rotation intervals are configuration defaults covered by tests and can be shortened operationally without changing MVP behavior.

## Capture protocol

### `capture.v1` envelope

The Pydantic API schema is the runtime owner and the extension consumes a generated or mechanically synchronized TypeScript contract. Unknown fields are rejected.

```text
protocol_version: "capture.v1"
client_version: bounded semver string
idempotency_key: high-entropy per user action (header, not body identity)
platform: "youtube" | "ntu_kaltura"
platform_id: validated YouTube video ID or Kaltura entry ID
canonical_url: normalized https URL on an allowlisted platform host
page_url: optional normalized supported page URL; no signed query fields
metadata:
  title, author, duration_sec, language, description, cover_url, chapters
caption:
  status: "available" | "unavailable"
  source: "official_cc" | "auto_caption" | null
  language: normalized language or null
  cues: ordered [{start_sec, end_sec, text}]
content_hash: SHA-256 of the server-defined normalized cue representation
```

The server recalculates canonical URL and content hash and does not trust client values for deduplication or storage ownership. It enforces HTTPS, exact hosts, platform ID format, finite/non-negative timings, `end >= start`, monotonic cue ordering, bounded text/metadata, current ingestion byte/cue/character limits, decompressed body ceilings, and a strict JSON media type. Caption URL, manifest URL, headers, cookies, token fields, audio, and video are not accepted by the schema.

`caption.status=unavailable` requires empty cues and null caption source/language. It creates or advances the item to `needs_asr` through the normal dispatch/completion contract. It does not represent a successful empty transcript and does not upload media.

### API surface

- Same-origin Web routes: pairing approval, paired-device list, and revoke; existing session plus exact-Origin/CSRF protection applies.
- Extension routes: pairing exchange/status and `POST /api/v1/browser-companion/captures`; authenticate with the dedicated Bearer resolver and exact extension-origin policy.
- Capture submission requires `Idempotency-Key`. Repeating the same key and normalized body returns the same public result; reusing it with a different body returns a stable conflict.
- Responses expose public item ID, lifecycle, platform, and safe action/error codes only. They never expose internal IDs, raw object keys, transcript bodies, signed URLs, or tenant identifiers.

## Platform adapters

### YouTube

- Match supported `youtube.com/watch` and `youtu.be` contexts and validate the 11-character video ID.
- Under the user gesture, read current player metadata/caption track descriptors and fetch the selected caption body from the browser context. Prefer the original-language official track, then original-language automatic track, preserving the existing server connector's language-selection semantics where inputs permit.
- Parse and normalize cues inside the adapter; do not pass the signed caption URL to Notebook Agent.
- If no usable track exists, submit `caption.status=unavailable` and surface `needs_asr`.
- If server-side ingestion returned `youtube_rate_limited`, Web detail/add-result copy offers “使用浏览器伴侣保存” rather than treating repeated tunnel retries as the durable recovery.

### NTULearn/Kaltura

- Support `ntulearn.ntu.edu.sg` pages containing an accessible Kaltura player and direct `ntulearnvideo.ntu.edu.sg` media pages.
- Resolve the Kaltura entry ID and allowlisted metadata/caption assets from the active authorized player context. Consume short-lived KS/signed URLs locally, then discard them.
- Normalize supported WebVTT/DFXP/SRT/player cue shapes into the common cue contract. Reject DRM/access failures as authorization/unavailable outcomes; never attempt to bypass them.
- Canonicalize the source to a stable allowed NTULearnVideo/NTULearn URL without session or signed query parameters. A platform-owned timestamp-link builder uses the verified Kaltura deep-link form; if a page cannot deep-link reliably, return the canonical video URL rather than inventing an unsafe URL.

Adapters share contract validation utilities but not page-specific selectors/player parsing. Fixture tests own every external shape so a site change fails as `page_adapter_changed` instead of producing incorrect content.

## Durable submission and worker flow

### Data model

Add migrations and models for:

- `browser_companion_pairing`: public challenge ID/hash, app user after approval, client label/version, expiry, consumed/approved timestamps, attempt/rate-limit fields.
- `browser_companion_grant`: public device ID, app user, token hash, scopes, client metadata, expiry, last-used timestamp, revoked/disabled timestamps.
- `browser_capture`: app user, public capture ID, idempotency digest/body digest, item/dispatch references, protocol/client version, caption availability, canonical raw-object key/cleanup intent, state, expiry/diagnostic timestamps, and safe error code.

Constraints/indexes enforce unique active identities and one result per tenant/idempotency key. Foreign keys and purge behavior must preserve existing item/deletion contracts. Store no raw credential, cookie, third-party session token, signed URL, or transcript body in these rows.

### Admission and storage

1. Authenticate grant and derive `UserScope` server-side.
2. Parse with a request-body ceiling and validate/normalize the full envelope before database/object-store side effects.
3. Under the existing tenant quota/deduplication locks, create/restore the `ContentItem`, create a capture/dispatch, and persist a deterministic object cleanup intent.
4. Put canonical `capture-transcript.v1` JSON to the tenant/platform/media/content-hash object key. A retry may safely repeat the deterministic put.
5. Mark the capture ready and publish the existing ingest dispatch. Broker failure follows existing compensation/retry semantics; no transcript bytes enter Redis/Celery messages.
6. The worker detects the ready browser capture for the dispatch, reads the bounded canonical object, revalidates and parses it, applies metadata, then reuses current chunking, embedding, item state, dispatch finalization, and completion-event paths without calling YouTube/Kaltura.

If a crash happens between cleanup-intent commit, object put, readiness, and publish, the same idempotency request or a bounded repair path converges the capture. Purge/soft-delete races retain the existing item-deleted and cleanup behavior. Do not hold a database transaction across object-store or broker I/O.

### Canonical transcript storage and reading

- Introduce a versioned platform-neutral canonical raw schema and parser owner instead of naming all objects `.json3`.
- Existing YouTube JSON3 objects remain readable without migration. TranscriptService dispatches by stored format/version and retains tenant-prefix checks and bounded object reads.
- New captures store normalized cues once. Web transcript blocks, chunking, and citations consume shared cue projections instead of independently parsing client JSON.

## Platform-aware product projection

- Extend platform enums and browser DTOs with `ntu_kaltura`; search all exhaustive platform branches when adding the value.
- Centralize source labels, canonical open URLs, timestamp URLs, thumbnail host rules, and supported capture actions. Do not add isolated YouTube/Kaltura conditionals in each consumer.
- Update capabilities, Add Videos guidance, item detail, transcript source links, citation URLs, lifecycle text, and `available_actions` so server state remains authoritative.
- Add a paired-device account surface and a compact browser-companion onboarding/recovery entry. The extension popup owns capture progress; the Web UI owns account approval/revocation and server-side rate-limit recovery guidance.
- Update CSP only for exact required Kaltura thumbnail hosts after verification; never permit a wildcard image source.

## Error and privacy contract

Stable safe categories include:

- `extension_pairing_required`, `extension_grant_expired`, `extension_grant_revoked`
- `unsupported_page`, `host_permission_required`, `page_not_authenticated`
- `captions_unavailable`, `page_adapter_changed`, `capture_payload_invalid`
- `capture_too_large`, `capture_protocol_unsupported`, `capture_conflict`
- `quota_exceeded`, `queue_unavailable`, `capture_upload_failed`

Logs/metrics contain only safe stage, platform, protocol/client version, status/error code, public or hashed correlation identifier, bounded counts, byte size, and duration. They exclude page titles, descriptions, cue text, URLs containing queries, raw exceptions from pages/providers, extension/Web tokens, and all third-party authorization material.

## Compatibility, rollout, and user control

1. Land forward-compatible schema/models and authenticated backend routes under the normal deployment admission contract; an unpaired caller has no capture authority.
2. Land canonical transcript parsing and platform-aware projections while preserving legacy JSON3 and server-side YouTube tests.
3. Build/test/package the extension against local/staging with a configured exact API origin and stable development extension ID.
4. An operator voluntarily installs and pairs the extension, then runs fixture pages, one public YouTube canary from an IP context where server acquisition is rate-limited, and one user-authorized NTULearn/Kaltura caption canary.
5. Publish installation/pairing guidance after capture telemetry and tenant isolation are verified. Keep the existing YouTube connector and temporary tunnel available during the pilot.

The extension is opt-in at the user/browser level. Installing and pairing it enables browser capture for that user; disconnecting the pairing, disabling the browser extension, or uninstalling it stops future browser captures. The existing server-side YouTube method remains available independently, so users can choose either method. Items already ingested through the extension remain normal knowledge items and continue to work without the extension.

If an application release itself must be reverted, the previous server release may temporarily report the installed extension as incompatible, but legacy save and existing item reads remain intact. Keep forward schema/data rather than destructively downgrading or deleting capture audit state.

## Trade-offs and deferred work

- Browser acquisition avoids the production YouTube IP path but depends on changing, non-official page/player contracts.
- A dedicated extension grant adds a credential type, but preserves the intentional isolation of Web cookies and MCP Bearers.
- Canonical object staging adds crash-repair work, but keeps multi-megabyte transcripts out of PostgreSQL and Redis and makes the worker path durable.
- Caption-only MVP leaves some videos in `needs_asr`; it avoids broad media permissions, resumable uploads, ASR staging, bandwidth/storage growth, and additional copyright exposure.
- Public extension-store distribution, Firefox/Safari, organization-wide Blackboard/LTI/REST installation, audio capture/upload, resumable transfer, and ASR execution are deferred.

## Implementation-time technical validations

These do not change MVP product behavior but must be resolved before their adapter is accepted:

- Record sanitized fixtures from the current YouTube and NTU Kaltura players and verify caption selection/fetch in an unpacked MV3 build.
- Verify the exact Kaltura deep-link parameter supported by NTULearnVideo; otherwise use the canonical source URL fallback.
- Verify the production extension ID/origin packaging strategy and exact CORS/host-permission allowlist before enabling pairing outside local development.
