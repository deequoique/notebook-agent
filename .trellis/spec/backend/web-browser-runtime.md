# Web Browser Runtime Contract

## Ownership and composition

- `app/api/app.py` owns the only browser-facing FastAPI application, including
  authentication, library, conversation, and link routes.
- `app/api/runtime.py` is the production dependency-composition root.
  Compatibility modules such as `app/web_api.py` may delegate to this root,
  but must not define another cookie parser, session resolver, CSRF boundary,
  route set, or error envelope.
- The public prefix is fixed at `/api/v1`. Do not advertise a configurable
  prefix that the canonical routers cannot honor.

## Email authentication contract

- Production browser login is email-only and uses:
  `POST /api/v1/auth/challenges`, `POST /api/v1/auth/verify`, and
  `GET|DELETE /api/v1/auth/session`.
- Keep raw session and CSRF credentials only in `__Host-kb_session` and
  `__Host-kb_csrf`. Unsafe browser requests require exact Origin validation and
  double-submit `X-CSRF-Token` validation.
- Browser errors use the bounded `{code, message}` envelope. Challenge
  acceptance must not distinguish existing, unknown, or rate-limited email
  addresses. Session DTOs expose `authenticated`, `login_channel`, and
  `expires_at`, never tenant, user, identity, or session IDs.

## Tenant and transport boundaries

- Authenticated conversation adapters preserve the resolved tenant's complete
  channel namespace: `channel`, `account_id`, and `external_user_id`. Never
  rebuild a legacy Telegram/WeChat identity as `web/web/<id>` and let
  `ChannelService` self-register a different tenant.
- The combined ASGI dispatcher selects MCP by `MCP_PATH` before dispatching to
  the browser application. Browser cookies never authenticate MCP, and MCP
  Bearer credentials never authenticate browser routes.
- Browser-companion capture is a third, isolated transport credential. Exact
  extension-origin routes use a hash-at-rest `capture:write` Bearer; Web
  cookies approve/list/revoke devices but never authenticate capture, and the
  capture Bearer never authenticates normal Web or MCP routes. See
  `browser-companion-capture.md`.
- Production composition must forward the configured channel service into the
  canonical browser app; retained conversation routes must not become a
  permanent 503 surface through omitted wiring.

## Conversation streaming contract

- The browser SSE route delegates one `ChannelService` execution. Identity
  resolution, message idempotency, and durable turn persistence remain owned by
  that service; the route must not start a second Agent execution or save a
  second response for a compatibility fallback.
- A grounded section is public only after its current-run Citation metadata is
  authorized, then follows `section_started -> text_delta* ->
  section_completed`. Technical interruption may replace completion with
  `section_aborted`; a client disconnect, cancellation, timeout, or incomplete
  section never persists a partial conversation turn.
- Empty or unsupported provider streaming before any section is public falls
  back to one whole-answer `text_delta` plus the final response. If a section
  has already been exposed, the failure is terminal and must not silently
  replay the answer.
- Event fields that do not apply to a lifecycle event are omitted from the JSON
  SSE record when null. Generated TypeScript therefore treats nullable
  event-specific fields such as `section_id`, `status`, and `reason` as
  optional; runtime lifecycle validation remains mandatory.

## OpenAPI and verification

- `scripts/export_web_openapi.py` constructs the real email-enabled production
  route composition with inert dependencies and no provider/network side
  effects. Generated JSON and TypeScript are checked in together.
- Regression tests must cover the production route set, challenge/verification
  safe-error matrix, authenticated library access, CSRF logout, legacy tenant
  affinity through a real `ChannelService`, and combined Web/MCP credential
  isolation.
- Release validation includes the focused credential-free backend workflow,
  the full Python suite with loopback HTTP available, all frontend gates, one
  Alembic head, and a 390×844 real-browser login/logout smoke with empty Web
  Storage and clean page-error/console buffers.
