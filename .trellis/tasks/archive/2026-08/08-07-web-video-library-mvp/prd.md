# Notebook Agent Web Video Library MVP

## Goal

Deliver the first mobile-first Web experience for Notebook Agent as a private
YouTube video library. The primary loop is login through an already trusted
Telegram or WeChat identity, browse a personal library, explicitly add one to
ten YouTube URLs, follow asynchronous ingestion, and read a useful video detail
page with chapters and the original transcript.

The Web product is a library and reading surface, not a general chat UI. The
existing Agent remains available through channels and appears in the Web UI only
as static onboarding copy for the first-ever empty library. That copy must not
trigger a model request.

## Product Requirements

### 1. Trusted channel-assisted Web login

- `/login` lets the user choose Telegram or WeChat, creates a short-lived login
  challenge, and displays a deterministic `/web-login CODE` command with copy
  and channel-open affordances when configured.
- The code is cryptographically random and stored only as an HMAC made with a
  dedicated server secret. A separate high-entropy browser exchange secret is
  hashed at rest and kept only in SPA memory. The challenge expires after ten
  minutes, is attempt-limited, channel-bound, and can approve at most one Web
  session. Expired, used, disabled, unavailable, cancelled, and network states
  have explicit safe UI and API outcomes.
- Public challenge creation is atomically limited per requester, globally, and
  by active challenge count. Only an HMAC of the trusted proxy-derived requester
  address is stored; retention cleanup is bounded per request.
- The existing signed loopback channel gateway remains private. The browser
  never calls it and no public proxy is added in front of it.
- A browser request can never submit or select `user_id`. It also cannot create a
  fake `ChannelIdentity`. The authenticated server-side Web session is the only
  source of `UserScope.app_user_id`.
- The session is an opaque, high-entropy cookie reference backed by PostgreSQL.
  The cookie is `HttpOnly`, `Secure` in production, `SameSite=Strict`, and never
  stored in Web Storage. Logout and expiry revoke the server record as well as
  deleting browser cookies.
- Every authenticated state-changing request verifies a session-bound CSRF
  token and exact same-origin metadata. No wildcard credentialed CORS is added.

### 2. Private library

- `/library` lists only the authenticated user's content and supports title,
  author, and `why_saved` search; filters for all, processing, completed,
  needs-action, failed, and archived; recent/oldest/title sorting; pagination;
  loading skeletons; empty/error/retry states; and an Add action.
- A true first-use empty library shows restrained Agent onboarding copy. A
  filtered empty result shows a normal filter-empty message and never the Agent
  onboarding state.
- Rows/cards show cover or a deterministic skeleton, title/URL fallback, author,
  save date, `why_saved`, and the normalized lifecycle state. Initial async
  submissions cannot fabricate title, author, duration, or cover metadata.
- Internal database identifiers, worker task IDs, raw provider failures, URLs of
  other tenants, and secrets never appear in an API response.

### 3. Explicit batch add

- The Add sheet accepts one URL per line, one to ten lines, plus an optional
  shared `why_saved`. It performs local/preflight normalization only.
- Submission calls the trusted `IngestSubmissionService` directly with the
  authenticated `UserScope`; it does not invoke the Agent or pending channel
  confirmation flow and does not synchronously fetch remote metadata.
- `Idempotency-Key` is a required documented HTTP header. Its MVP contract is
  explicit: for the same authenticated user, normalized YouTube item, and key,
  a retry returns a stable result without republishing work. The raw header is
  hashed/namespaced before persistence and never logged. Full byte-for-byte
  batch response replay is not claimed.
- Per-item partial results preserve input order and use only the existing public
  statuses: `queued`, `already_exists`, `invalid_url`, `unsupported_url`,
  `queue_unavailable`, `create_failed`, and `quota_exceeded`.
- PostgreSQL admission applies per-user and global active/daily limits plus a
  per-user storage ceiling. Batch, channel save, and Web retry use the same lock
  order and policy; replay and already-existing items do not consume new-item
  quota. Every new dispatch, including a failed-item retry, counts toward
  separate per-user and global daily dispatch limits.
- A ten-URL Web request has one bounded publication deadline; if Redis is down,
  remaining durable dispatches fail quickly with `queue_unavailable` rather than
  consuming the per-item broker timeout ten times.

### 4. Lifecycle and management

- The API aggregates `ContentItem` and its latest `IngestDispatch` into only six
  Web states: `queued`, `processing`, `ready`, `needs_action`, `failed`, and
  `archived`.
- A pending item whose latest dispatch failed with `queue_unavailable` is
  `failed`, never `queued`. Each response includes stable `error_code` and
  `available_actions` values instead of `fail_reason` or exception text.
- Users can edit `why_saved`, soft-archive, restore, retry eligible failures,
  open the canonical YouTube source, and log out. Archive is represented by
  `archived_at`; permanent delete is not provided.
- Archived ready items are excluded from all Agent retrieval/item/open-at paths,
  so Web archive has one consistent meaning across Web and channel products.
- Content and dispatches use public, non-sequential IDs at the HTTP boundary.
  Retry reuses the existing tenant-safe ingestion submission semantics and does
  not duplicate an active job.

### 5. Video detail and transcript reading

- `/videos/:id` is the primary detail page: cover, source link, lifecycle,
  optional metadata, editable `why_saved`, optional chapters, readable
  transcript, and management actions.
- Retrieval `Segment` rows are semantic search blocks with overlap and must not
  be presented as a full transcript.
- The transcript service loads the item's tenant-owned original MinIO JSON3
  object, validates and parses cues, coalesces them into chronological,
  non-overlapping human-readable `TranscriptBlock` values, and returns bounded
  progressive pages with stable cursors.
- Chapters and transcript timestamps open the canonical YouTube URL at the
  selected time.
- Summary generation is out of scope. The API exposes the summary capability as
  unavailable unless real stored summary data exists; the frontend renders a
  summary section only for a non-empty backend value and otherwise shows no tab,
  placeholder, button, or fake text.

### 6. Capabilities, errors, and deployment

- A public capabilities endpoint reports supported platform `youtube`, maximum
  batch size `10`, enabled login channels, archive support, and summary support.
- The new FastAPI/OpenAPI service is independent of the loopback HMAC gateway
  and serves the production React build from the same origin when available.
- Safe, stable error codes cover auth, CSRF, validation, tenant ownership,
  ingestion, storage, and lifecycle failures. Logs contain internal correlation
  identifiers and classes/codes only, not code/session/CSRF values, raw URLs,
  transcript text, external identities, provider messages, or tracebacks in API
  bodies.
- Existing channel, Agent, retrieval, worker, migration, and tenant isolation
  behavior remains compatible.
- Channel identities can still auto-register tenants, so global ingestion
  limits are a required cost circuit breaker. This MVP is private through
  channel-assisted identity, but it does not claim invite-only enrollment.

## UX Requirements

- Reference viewport is 390x844. Desktop content is centered at approximately
  960-1120 px with one simple top bar and no complex navigation.
- Visual direction is pale, editorial, content-first, and deliberately quiet:
  PLAY-style library density, Readwise-style transcript reading, NotebookLM-like
  private-source confidence, and restrained Perplexity-style hierarchy.
- The UI must be keyboard usable, screen-reader labeled, touch friendly, and
  respectful of `prefers-reduced-motion`. Status must never rely on color alone.
- Required routes are `/login`, `/library`, and `/videos/:id`; account UI is a
  minimal popover/sheet containing logout.

## Out of Scope

- General Web chat, chat history, free-form Agent save, pending-confirmation
  cards, summary generation, transcript summarization, or retrieval visualization.
- Embedded player synchronization, watched-state UX, ratings, tags, playlists,
  bulk editing, browser extensions, PWA/offline mode, dark mode, admin dashboards,
  user profile management, or multi-device session management UI.
- Bilibili, WeChat articles, file uploads, article ingestion, permanent delete,
  or exposing the private channel gateway to a browser/network.

## Acceptance Criteria

- [ ] A fresh migration upgrade creates Web challenge/session storage, public
  content IDs, and archival state; downgrade/upgrade is
  verified without losing pre-existing user/content rows.
- [ ] Telegram and WeChat signed channel messages can approve only a matching,
  live challenge via `/web-login CODE`; concurrency produces at most one session,
  and codes/tokens never appear in persisted plaintext or diagnostics.
- [ ] Web auth tests prove expiry, used challenge, disabled user, logout,
  server-side revocation, Secure/HttpOnly/SameSite cookies, CSRF, same-origin
  rejection, and that browser payloads cannot choose a tenant.
- [ ] Library/add API tests prove cross-tenant 404 behavior, search/filter/sort/
  pagination, 1-10 preflight, metadata-free submission, ordered partial results,
  bounded ten-item broker failure, same-user/item/key replay, and no internal ID
  or raw failure leakage.
- [ ] Lifecycle tests cover every Web state and especially pending item plus
  failed `queue_unavailable` dispatch => `failed`, with correct actions.
- [ ] Management tests prove edit, archive, restore, and eligible retry remain
  tenant-bound, archived content disappears from Agent retrieval, and permanent
  deletion is unavailable.
- [ ] Transcript tests prove JSON3 is read from the tenant-owned raw object,
  `Segment` is not used, blocks are chronological/non-overlapping, pagination is
  bounded, timestamp links are correct, and missing/corrupt/oversized objects fail
  with safe codes.
- [ ] React tests cover login states, true-first-empty versus filtered-empty,
  batch partial results, lifecycle rendering/actions, optional-summary omission,
  transcript paging, and logout/CSRF request behavior.
- [ ] A mobile browser smoke at 390x844 completes login simulation, add, library,
  detail/transcript, edit/archive/restore/retry, and logout without horizontal
  overflow or critical accessibility/console errors.
- [ ] Existing Python tests, frontend lint/typecheck/tests/build, migration
  current/check/roundtrip, `git diff --check`, and Trellis validation pass or any
  environment-only gap is explicitly recorded.
- [ ] Changes are committed only on `codex/web-video-library-mvp`, pushed only to
  `origin`, and proposed to `upstream/main`; `.omx/` is never staged.

## Notes

- Parent: `08-04-video-text-kb`.
- The linked project conversation and earlier image references were read in full
  before this PRD was written; the distilled evidence is in `research/`.
