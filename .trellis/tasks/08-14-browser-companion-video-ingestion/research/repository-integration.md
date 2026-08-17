# Repository integration findings

Reviewed on 2026-08-14 for the browser-companion planning task. This note records repository-answerable constraints; it does not authorize implementation.

## Existing acquisition and ingestion boundaries

- `app/connectors/base.py` already defines the platform connector result boundary: `ItemMeta`, timed `Cue` values, `TextResult`, `NeedsExtension`, and `NeedsASR`.
- `app/ingest/tasks.py:240-330` currently obtains metadata and transcript from a connector, validates non-empty content and configured content limits, chunks cues, persists the raw body, embeds segments, and owns lifecycle transitions. Browser capture should enter before the same validation/storage/chunking path rather than duplicating those responsibilities in the extension.
- `app/ingest/validate.py` enforces non-empty usable cues plus byte, cue-count, and character limits. `app/config.py:225-240` defaults those limits to 5 MB raw bytes, 50,000 cues, 1,000,000 transcript characters, 5,000 segments, and 2,000,000 embedding characters.
- `tests/test_tasks.py:294` proves oversized connector output is rejected before object storage or embedding. `tests/test_tasks.py:250-283` covers `NeedsASR`; the suite also covers `NeedsExtension`, terminal notifications, tenant-scoped transcript reads, object-size rejection, and dispatch idempotency.
- Raw object keys and transcript parsing currently carry a `.json3`/YouTube assumption. `app/web/transcript.py:15` imports `parse_json3`, while `app/ingest/tasks.py` always writes JSON with a `.json3` suffix. The browser protocol needs a canonical platform-neutral raw schema with explicit version/content type.

## Authentication and extension pairing

- `.trellis/spec/backend/web-browser-runtime.md` makes the current browser boundary intentionally same-origin. Raw session and CSRF credentials are held only in `__Host-kb_session` and `__Host-kb_csrf`; unsafe requests require exact Origin validation and double-submit `X-CSRF-Token` validation.
- `app/api/app.py:350-445` resolves browser identity only from the session cookie and applies the protected-mutation CSRF boundary. `app/api/auth_routes.py` also checks same-origin fetch metadata on challenge/session operations.
- A `chrome-extension://...` caller cannot safely reuse this contract, and the extension must never extract/copy the Web cookie. Pairing therefore needs a dedicated extension-device authorization flow whose approval page is served on the authenticated Notebook Agent Web origin.
- The repository has a useful security pattern in `McpAccessGrant` and `McpGrantService`: random Bearer material is stored only as a hash and can expire, rotate, revoke, or disable. However, `.trellis/spec/backend/web-browser-runtime.md` explicitly isolates MCP Bearer credentials from browser routes. Extension credentials need their own model/scope and API dependency; only the pattern should be reused.
- Recommended pairing shape for technical design: the extension creates a short-lived, single-use pairing challenge; the user opens Notebook Agent and approves it while signed in; the backend issues a least-privilege capture credential once; the extension stores it in extension-local storage and offers disconnect; the Web account surface can list and revoke paired devices. Exact TTL/rotation defaults can be technical configuration rather than a product-blocking choice.

## Save API, quotas, and tenant isolation

- `app/api/library_routes.py` accepts bounded URL batches with an idempotency key and tenant-scoped identity. The raw idempotency key is hashed before it enters the service request key.
- `app/ingest/submission.py` owns URL normalization, per-tenant/global active and daily quotas, deduplication, dispatch creation, and publish compensation. Browser captures should reuse these controls through a new submission service method instead of bypassing them.
- The current URL normalizer accepts YouTube only (`app/ingest/submission.py:408`). Platform registration must be generalized before Kaltura references and direct captured-content submissions can share deduplication/lifecycle behavior.
- Extension capture needs a payload-specific request-body ceiling at the HTTP boundary in addition to the existing post-parse ingestion limits. It should reject compressed/decompressed size abuse, invalid cue ordering/timing, excessive metadata, and unsupported protocol versions before external writes.

## Platform-specific assumptions to remove

- `app/models.py:42` lacks an NTULearn/Kaltura platform value.
- `app/ingest/tasks.py:182` composes only `YouTubeConnector`.
- `app/web/transcript.py:15` parses every stored transcript as YouTube JSON3 and creates timestamps by adding a generic `t` query.
- `app/agent/services.py:52` emits YouTube-specific timestamp citations only for the `youtube` platform.
- `app/api/app.py:282-287` permits only YouTube thumbnail hosts in CSP.
- `web/src/library/AddVideosDialog.tsx:117` and `web/src/videos/VideoDetailView.tsx:124` present YouTube-specific source and action language.

The implementation design must introduce a platform-aware source-link/timestamp builder and a canonical captured-transcript schema rather than scattering more platform conditionals.

## Build, packaging, and deployment conventions

- The repository has one Vite/TypeScript/React package under `web/`; no browser-extension package, Manifest V3 manifest, or extension build pipeline currently exists.
- The Web package uses Node 22+, pnpm, strict TypeScript compilation, ESLint, Vitest, and generated OpenAPI types. A new extension package should follow the same toolchain and testing standards while producing an independently packageable MV3 artifact.
- Keeping the extension in a separate top-level package avoids bundling extension scripts into the server-hosted SPA. Users can install, disable, or uninstall it independently, while the server-side YouTube path and already saved items remain available.

## Test seams required by existing quality patterns

- Parser fixtures for YouTube and Kaltura caption/metadata shapes, including no-track, malformed, oversized, duplicate, reordered, expired-signed-URL, and page-change cases.
- API tests for pairing issue/approve/exchange/revoke, credential hashing and scope, CORS/origin behavior, tenant isolation, idempotency, quotas, protocol versions, payload ceilings, and privacy-safe errors/logs.
- Service/worker tests proving browser content reaches the existing validation/chunk/storage/embed path without a server attempt to fetch captured signed URLs.
- Regression tests for existing server-side YouTube ingestion, `needs_extension`, `needs_asr`, transcript reads, Web/MCP credential isolation, generated OpenAPI types, and frontend library/detail behavior.
- Extension unit tests should isolate page adapters from Chrome APIs; a packaged-extension smoke should verify minimum permissions and capture on fixture pages before any live-site canary.
