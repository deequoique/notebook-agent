# Notebook Agent Web Video Library MVP - Technical Design

## 1. Architecture and trust boundaries

The existing loopback gateway remains the sole channel bridge. The new Web
surface is a separate same-origin FastAPI application:

```text
React SPA ---- HTTPS /api/v1 ---- FastAPI ---- WebSession ---- UserScope
                                      |                         |
                                      |                         +-- ContentLibraryService
                                      |                         +-- IngestSubmissionService
                                      |                         +-- TranscriptService
                                      |
Telegram/WeChat -> LangBot -> loopback HMAC gateway -> ChannelService
                                                    -> /web-login challenge approval

Celery worker -> YouTube metadata/subtitle -> MinIO raw JSON3 -> Segment retrieval blocks
                                                   |
                                                   +-- TranscriptService reads raw JSON3
```

`UserScope(app_user_id)` is created only after a valid database Web session and
is the minimal base contract of the channel-specific `TenantContext`. Route DTOs
do not contain `user_id`. Services such as ingestion that need only tenant
ownership accept `UserScope`; channel approval still requires full
`TenantContext`. Every library query includes the scope in its predicate;
another tenant's public ID returns the same `not_found` response as a missing
item.

FastAPI dependencies create one SQLAlchemy `Session` per request. Authentication
is explicit dependency injection, not a global `ContextVar`, client-controlled
Pydantic context, or a forged `TenantContext`/`ChannelIdentity`.

## 2. Persistence changes

### 2.1 `web_login_challenge`

- `id BIGINT PK`
- `public_id TEXT UNIQUE NOT NULL`: opaque, non-sequential challenge identifier
- `code_hash TEXT UNIQUE NOT NULL`: HMAC-SHA256 of a human-copyable code using a
  dedicated `WEB_AUTH_SECRET`
- `browser_token_hash TEXT NOT NULL`: SHA-256 of a separate 256-bit exchange
  secret held only in SPA memory
- `requester_hash TEXT NOT NULL`: HMAC of the trusted proxy-derived requester
  address; the raw address is never persisted
- `target_channel TEXT NOT NULL`: `telegram` or `wechat`
- `approved_app_user_id BIGINT NULL FK app_user`: set only by the trusted command
- `approved_by_identity_id BIGINT NULL FK channel_identity`: audit proof of the
  approving trusted channel identity
- `expires_at`, `approved_at`, `consumed_at`, `cancelled_at`, `created_at`
- `attempt_count INTEGER NOT NULL DEFAULT 0`

Approval uses `SELECT ... FOR UPDATE`, checks target channel, active state,
expiry, attempt limit, and a non-disabled resolved identity, then sets the
approved user/identity and timestamp. Session exchange requires both public ID
and browser token, also locks the row, and sets `consumed_at`, so one challenge
creates at most one Web session.

Challenge creation takes one PostgreSQL transaction advisory lock before
bounded retention cleanup, per-requester/global/active counts, and insert. Each
request deletes at most 100 expired challenges and 100 old sessions. The public
error is one non-disclosing `rate_limited` code.

### 2.2 `web_session`

- `id BIGINT PK`, `public_id TEXT UNIQUE NOT NULL`
- `token_hash TEXT UNIQUE NOT NULL`, `csrf_token_hash TEXT NOT NULL`
- `app_user_id BIGINT NOT NULL FK app_user ON DELETE CASCADE`
- `login_channel TEXT NOT NULL`
- `created_at`, fixed `expires_at`, and `revoked_at`

The cookie contains only the raw random session token. PostgreSQL determines
ownership and validity. CSRF uses a separate readable random cookie plus an
`X-CSRF-Token` header; the hash is bound to the current session row.

### 2.3 Content and HTTP idempotency

- Add `content_item.public_id TEXT UNIQUE NOT NULL` and backfill existing rows.
- Add `content_item.archived_at TIMESTAMPTZ NULL` and an index supporting the
  tenant/archive/save-date list path.

No batch receipt table or response cache is added. The endpoint documents the
existing durable contract: a raw `Idempotency-Key` is hashed and namespaced to
the authenticated user before becoming the internal `request_key`; the same
user/item/key replays the same dispatch and never republishes. A differently
shaped batch using the same header is not claimed to be byte-for-byte HTTP
idempotent. This keeps the MVP aligned with the proven `(request_key, item_id)`
constraint instead of adding crash-recovery coordination solely for response
caching.

The Web adapter supplies one total publish deadline for the whole batch.
`IngestSubmissionService` passes the remaining time to the existing bounded
publisher; when no time remains it still preserves each already-created durable
dispatch but marks it `queue_unavailable` without another socket wait. Channel
callers retain their current per-action behavior. The batch limit moves to one
shared constant used by preflight and capabilities.

Ingestion admission is also transaction-bound. The stable order is global
PostgreSQL advisory lock, tenant `AppUser` row lock, quota counts, item/dispatch
write, commit, then broker publication. The shared policy covers Web batch,
channel actions, and Web retry. Defaults bound active work per user/globally,
daily new items per user/globally, and total items per user; global limits remain
necessary because channel identities may auto-register more than one tenant.
Daily dispatch limits are checked for both first attempts and retries, so a
caller cannot exchange idempotency keys for unbounded repeated worker cost.

No summary column is added because there is no current stored summary source.
Capabilities return summary unavailable, and the DTO keeps an optional summary
field for truthful future stored data only.

## 3. Authentication flow

1. `POST /api/v1/auth/challenges` validates an enabled target channel, creates
   code/public reference/browser secret with a ten-minute TTL, and returns the
   raw code and browser secret once.
2. The SPA displays `/web-login CODE`; the browser secret stays only in React
   memory, and neither secret enters local/session storage. Status/exchange calls
   send the public ID in a body and the browser secret in `Authorization`.
3. The signed channel gateway resolves/registers the trusted identity before the
   deterministic `/web-login` handler. It approves the matching challenge and
   returns canonical safe copy; the Agent is not called.
4. `POST /api/v1/auth/sessions` atomically exchanges an approved challenge and
   matching browser secret once, creates a new server session and CSRF secret,
   and sets `__Host-` cookies.
5. Auth dependencies hash the cookie, load the live session plus non-disabled
   `AppUser`, enforce fixed server-side expiry/revocation, and return `UserScope`.
6. Unsafe authenticated methods validate exact configured `Origin`, reject
   cross-site `Sec-Fetch-Site`, require cookie/header CSRF equality, and compare
   the stored hash in constant time.
7. `DELETE /api/v1/auth/session` revokes the row and deletes both cookies.

Challenge/session values, login commands, CSRF values, channel external IDs, and
cookie headers are excluded from application logs and response error details.

## 4. Service and API boundaries

### 4.1 Modules

- `app/channels/types.py`: `UserScope` plus the channel-specific
  `TenantContext(UserScope)`; framework-neutral enums/value objects stay with the
  Web services.
- `app/web/auth.py`: challenge/session creation, approval, exchange, resolution,
  revocation, hashing, and safe auth errors.
- `app/web/library.py`: tenant-scoped list/get/update/archive/restore/retry and
  lifecycle projection.
- `app/web/transcript.py`: bounded raw-object read, JSON3 parsing, coalescing, and
  cursor pagination.
- `app/api/auth_schemas.py` and `app/api/library_schemas.py`: Pydantic HTTP DTOs
  only.
- `app/api/auth_routes.py` and `app/api/library_routes.py`: explicit auth,
  session scope, CSRF/origin, validation, and service adapters.
- `app/api/app.py`: FastAPI composition, exception mapping, routes, security
  headers, OpenAPI, and optional SPA static serving.

The service layer remains independent of FastAPI and is testable with isolated
SQLAlchemy sessions/fake object stores. `IngestSubmissionService` is reused after
its input type is narrowed to `UserScope`; channel `TenantContext` remains a valid
subtype. No real or fake channel identity is created for a browser.

### 4.2 HTTP surface

Public:

- `GET /api/v1/capabilities`
- `POST /api/v1/auth/challenges`
- `POST /api/v1/auth/challenges/status`
- `POST /api/v1/auth/sessions`
- `GET /api/v1/health`

Authenticated:

- `GET /api/v1/auth/session`
- `DELETE /api/v1/auth/session`
- `GET /api/v1/library/items`
- `POST /api/v1/library/items:batch` (`Idempotency-Key` required)
- `GET/PATCH /api/v1/library/items/{item_public_id}`
- `POST /api/v1/library/items/{item_public_id}:archive`
- `POST /api/v1/library/items/{item_public_id}:restore`
- `POST /api/v1/library/items/{item_public_id}:retry`
- `GET /api/v1/library/items/{item_public_id}/transcript`
- `GET /api/v1/ingest-dispatches/{dispatch_public_id}`

All responses use public IDs and a stable envelope for safe errors. The OpenAPI
schema documents enums, limits, cookies, CSRF header, and `Idempotency-Key`.

## 5. Library projection and lifecycle

Library queries join each item to only its latest dispatch by attempt/id. Search
uses case-insensitive matching over title, author, and `why_saved`; filters map to
the normalized lifecycle. Pagination is bounded and deterministic. The response
includes `total`, page metadata, and `is_true_first_empty`, which is true only
when the tenant has no archived or active content at all.

Lifecycle precedence:

1. `archived_at != NULL` -> `archived`.
2. item state `ready` -> `ready`.
3. item state in `needs_extension`, `needs_asr`, `no_text` -> `needs_action`.
4. latest dispatch `failed` or item state `failed` -> `failed`.
5. dispatch `running` or item state in `fetching`, `chunking`, `embedding` ->
   `processing`.
6. item state `pending` with latest dispatch `pending/enqueued` -> `queued`.
7. inconsistent/missing-dispatch pending state -> `failed/missing_dispatch`.

The safe error allowlist includes `queue_unavailable`, `ingestion_failed`,
`transient_fetch_failed`, `item_missing`, `missing_dispatch`,
`transcript_unavailable`, and `transcript_invalid`. Unknown `fail_reason` values
collapse to `ingestion_failed`.

Actions are derived, not client-supplied authority: all active items allow
`edit_why_saved` and `archive`; archived items allow `restore`; terminal failed
states without an active dispatch allow `retry`; items with a URL allow
`open_source`.

Archive is one product-wide visibility boundary. Every Agent retrieval path
(`vector_search`, `bm25_search`, neighbors, direct item lookup, and open-at)
adds `ContentItem.archived_at IS NULL`. Archiving does not revoke an active
Celery task or delete MinIO/Segment data; restore reveals the worker's latest
truth.

## 6. Transcript projection

The service requires a tenant-owned ready item and a non-empty `raw_object_key`.
It checks object size before reading and applies a hard byte cap. JSON3 is parsed
through the existing validated parser. It never queries `Segment` for transcript
text.

The S3/MinIO adapter moves to a small shared `app/object_store.py` boundary so
the API does not import the Celery worker module. The reader also verifies the
stored key starts with the authenticated user's path prefix before any object IO.

Coalescing rules:

- preserve cue order after validating finite, non-negative timing;
- normalize whitespace and remove exact adjacent duplicates;
- merge nearby cues while the paragraph remains below bounded duration and text
  limits;
- force each next block start to be at least the previous block end, producing
  monotonically increasing, non-overlapping blocks;
- encode `content_hash` plus next ordinal in the opaque cursor so a re-ingest
  cannot mix pages from two transcript revisions;
- return at most the requested capped page and an opaque next cursor.

Missing, oversized, corrupt, or private-store errors return only a safe code.
Source timestamp URLs are constructed from the already stored canonical URL and
integer seconds.

## 7. Frontend architecture and visual system

The SPA uses React + TypeScript + Vite, the current `react-router` package for
the three route groups, TanStack Query for server state, generated
`openapi-typescript` declarations, and a small tokenized CSS layer. Native forms
and an HTML `<dialog>` implement the batch editor; no handwritten response
schema, icon package, utility-CSS framework, form library, global state store,
animation library, or heavy component suite is added.

Feature folders:

```text
web/src/
  api/       fetch client, generated schema, error mapping
  app/       router, tenant-cache lifecycle, shell
  auth/      login challenge/session/logout
  library/   list, filters, add dialog, lifecycle
  videos/    detail, chapters, transcript, management
  styles.css tokenized responsive layout and accessibility rules
```

The chosen aesthetic is a quiet editorial field: warm paper background, ink
text, desaturated blue-grey metadata, and a single signal-yellow accent drawn
from the PLAY reference. A distinctive serif is limited to titles; readable
sans-serif body text carries transcript density. Cards use fine rules and
spacing, not generic floating purple gradients. Motion is short and purposeful,
with reduced-motion fallbacks.

TanStack Query owns remote state. URL search params own filters/sort/page. Local
component state owns open sheets and unsaved input. Session secrets never enter
Web Storage; the CSRF cookie is read only to copy into the request header.
Logout and every `401` clear the QueryClient immediately so a later login cannot
briefly render a previous user's cache.

## 8. Migration, rollout, and rollback

- Migration upgrades from `c7e8a91b2d34`, backfills public content IDs, creates
  Web tables/indexes, and adds archival/idempotency fields.
- Existing gateway and worker processes remain compatible with additive schema.
  Item creators are updated to set public IDs; migration server defaults protect
  rollout order where needed.
- The Web server is a separate CLI process and does not change the loopback bind
  restriction. Production serves the built SPA and API on one TLS origin.
- Rollback first stops the Web server. Channel gateway/worker continue. A code
  rollback should keep the additive schema; database downgrade is tested but not
  the preferred production response.

## 9. External guidance adopted

- FastAPI dependencies and explicit Header declarations for auth and
  `Idempotency-Key`.
- Starlette cookie flags, without Python-3.14-only partitioned cookies.
- OWASP server-side session expiry/revocation, session rotation, CSRF token plus
  Origin/Fetch-Metadata checks, and short single-use challenge guidance.
- SQLAlchemy Session-per-request; no shared mutable Session or global implicit
  tenant context.
- Same-origin deployment with no credentialed wildcard CORS.
- React Router 8 `react-router` imports, Vite development-only proxy semantics,
  terminal-state polling stop, native dialog accessibility, reduced motion, and
  a Node `>=22.22.2` toolchain floor for the selected current frontend stack.

The full source packet and remaining uncertainties are summarized in
`research/product-reference-and-code-audit.md`.
