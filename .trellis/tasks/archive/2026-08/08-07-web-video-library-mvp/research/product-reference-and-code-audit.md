# Web Video Library MVP - Product Reference and Code Audit

## Source of product truth

The user asked for complete execution of the final instruction set in the linked
ChatGPT project conversation and explicitly asked that earlier visual references
also be reviewed. The full conversation was read before planning. This document
stores a durable, implementation-oriented distillation rather than copying the
conversation verbatim.

## Product loop distilled from the conversation

1. Login to the Web product through a one-time code confirmed by an existing
   trusted Telegram or WeChat identity.
2. Land in a private YouTube library, not a chat application.
3. Explicitly add one to ten URLs with optional shared `why_saved`; return
   ordered partial results and start only asynchronous processing.
4. Show truthful lifecycle progress and metadata skeletons until the worker
   fetches metadata/subtitles.
5. Open a content-first detail page with chapters and a readable original
   transcript, then edit/archive/restore/retry as allowed.
6. Render a stored summary only if one actually exists; do not generate or fake
   one in this MVP.

## Visual references distilled

- PLAY references: compact mobile library rows, clear media covers, strong but
  restrained status hierarchy, and bottom-sheet interactions.
- Readwise Reader references: transcript/reading typography, calm line length,
  timestamps as secondary navigation, and content over chrome.
- NotebookLM references: private-source confidence and explicit source context.
- Perplexity references: pale surfaces, fine dividers, limited accents, and
  quiet information hierarchy.
- Target viewport is 390x844; desktop widens the reading field but does not add a
  dashboard/sidebar product model.

## Existing code that must be reused

- `IngestSubmissionService` already provides tenant-bound YouTube URL preflight,
  a fixed batch maximum of ten, ordered partial results, request-key replay,
  minimal pending `ContentItem` creation, durable `IngestDispatch`, bounded broker
  publication, and no remote metadata fetch on submission.
- The worker already claims dispatches conditionally, fetches YouTube metadata
  and JSON3 subtitles, stores the raw JSON3 object in MinIO, chunks/embeds into
  `Segment`, and writes only safe dispatch failure codes.
- `ContentItem` already stores title, author, cover, duration, chapters,
  `why_saved`, raw object key, source, and ingestion state.
- Tenant isolation is currently enforced explicitly by channel-derived
  `TenantContext` in identity, conversation, Agent, retrieval, and ingestion
  services. The Web layer must preserve this explicit pattern with `UserScope`.
- The loopback HMAC gateway is deliberately private and deployment requires it to
  share a network namespace with the LangBot bridge. It cannot become the Web
  API.

## Existing code that cannot satisfy the Web requirement alone

- There is no public Web API, browser session, CSRF boundary, OpenAPI contract,
  list/detail/management service, React app, or frontend build.
- `TenantContext` requires a real channel identity; a browser must not fabricate
  one. A narrower server-trusted user scope is required.
- `Segment` is a semantic retrieval block with overlap, so rendering ordered
  segments would duplicate text and is not a faithful transcript. The original
  MinIO JSON3 cue stream is the correct source.
- `IngestDispatch.public_id` exists but internal `ContentItem.id` is currently the
  only item identifier. HTTP routes require a public content identifier.
- `fail_reason` must stay internal. A lifecycle projector is required to combine
  item and latest dispatch truth into safe Web states and error codes.

## Official backend/security guidance adopted before code edits

- FastAPI response cookies and dependencies:
  <https://fastapi.tiangolo.com/advanced/response-cookies/>
  and <https://fastapi.tiangolo.com/tutorial/dependencies/>.
- Starlette cookie/middleware behavior:
  <https://www.starlette.io/responses/> and
  <https://www.starlette.io/middleware/>. Do not use client-readable
  `SessionMiddleware` as the PostgreSQL-backed session implementation.
- OWASP Session Management and CSRF Cheat Sheets:
  <https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html>
  and <https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html>.
  Sessions are opaque server references with server-enforced expiry/revocation;
  writes use session-bound CSRF plus exact-origin checks.
- OWASP one-time secret guidance:
  <https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html>
  and <https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html>.
  Codes are short-lived, hashed, attempt-limited, and single-use.
- SQLAlchemy Session-per-request guidance:
  <https://docs.sqlalchemy.org/en/20/orm/session_basics.html>. A mutable Session is
  not shared between browser requests.
- OpenAPI Header parameters and the current, expired IETF idempotency draft:
  <https://spec.openapis.org/oas/v3.1.0#parameter-object> and
  <https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header>.
  The project adopts an explicit versioned API contract but does not describe the
  expired draft as a final RFC.

## Official frontend guidance adopted before scaffold edits

- React 19.2, Vite 8, React Router 8, TanStack Query 5, Tailwind 4, Vitest 4,
  Testing Library, and jsdom current metadata were checked on 2026-08-07. The
  exact latest candidate set requires Node `>=22.22.2`; package pins and the
  lockfile must preserve a verified compatible set.
- React Router 8 uses `react-router`; `react-router-dom` is removed. The SPA host
  must fall back to `index.html` for direct `/videos/:id` navigation:
  <https://reactrouter.com/how-to/spa>.
- Vite `server.proxy` is development-only and secrets must never use `VITE_*`:
  <https://vite.dev/config/server-options> and
  <https://vite.dev/guide/env-and-mode>.
- TanStack Query owns remote library/detail/transcript/lifecycle state; polling
  is enabled only for non-terminal lifecycle values and stops for terminal data:
  <https://tanstack.com/query/v5/docs/framework/react/guides/polling>.
- Tailwind v4 uses the official Vite plugin rather than legacy PostCSS setup and
  targets modern browsers:
  <https://tailwindcss.com/docs/installation/using-vite>.
- The Add sheet uses a real labeled form and native modal `<dialog>` semantics;
  status updates are accessible, zoom is preserved, motion is reduced on request,
  and controls meet WCAG 2.2 target sizing:
  <https://www.w3.org/WAI/tutorials/forms/>,
  <https://www.w3.org/WAI/WCAG21/Techniques/html/H102>, and
  <https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum>.
- Vitest uses `jsdom`; Testing Library assertions target roles/labels and each
  test owns a QueryClient with retries disabled. Real browser smoke remains
  required for native dialog focus/inert and mobile layout.

## Remaining external uncertainties

- The exact production TLS/reverse-proxy origin and channel deep links are
  deployment configuration, so secure defaults and explicit environment values
  are required.
- Manual real-channel login smoke requires the user's existing Telegram/WeChat
  runtime and is distinct from signed gateway automation.
- Browser support follows the current Vite/Tailwind modern baseline; supporting
  materially older browsers would require a separate product decision.
- Zod, Lucide, and lint-package exact versions must be verified before adding
  them because the initial frontend research intentionally rejected unproven
  extra dependencies.
