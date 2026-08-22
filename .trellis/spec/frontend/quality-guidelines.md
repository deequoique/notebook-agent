# Quality Guidelines

> Required frontend checks and review boundaries.

---

## Overview

Frontend changes must preserve tenant privacy, honest asynchronous states, accessible mobile interaction, and OpenAPI contract alignment. The smallest supported dependency set is preferred.

---

## Forbidden Patterns

- Browser-supplied tenant, user, channel identity, database, queue task, or object-store identifiers.
- Auth secrets in Web Storage, query strings, logs, rendered text, or analytics.
- Wildcard credentialed CORS or direct browser access to the loopback HMAC gateway.
- LLM calls for the empty state or summary generation.
- Building transcript text from search segments.
- Raw `fetch` calls outside `src/api/client.ts`.
- Manual edits to `openapi.json` or `schema.d.ts`.
- State-changing GET requests.
- Mock data in production modules.
- Silent catch blocks that convert all failures into an empty state.

---

## Required Patterns

- Same-origin `fetch` with `credentials: "same-origin"`.
- CSRF cookie copied to `X-CSRF-Token` for unsafe requests.
- `Idempotency-Key` on add and retry.
- A `session_invalid` 401 clears and replaces the full private query client;
  recoverable operation-level 401s remain local to their form.
- Server-provided `available_actions` controls management buttons.
- Loading, error, filtered-empty, and true-first-empty are distinct states.
- API typo routes remain JSON and must never fall through to the SPA shell.
- Production assets are built by Vite and served from the same origin as FastAPI.

---

## Testing Requirements

Run from `web/`:

```text
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm check:api
```

Tests must cover lifecycle copy and polling, true-first-empty behavior, partial
batch outcomes, email challenge/verification, recoverable invalid codes,
duplicate-submit protection, cache rotation, detail chapters/transcript, and
server-derived actions. Conversation streaming tests must cover the ephemeral
section lifecycle, one-delta fallback before any public section, abort cleanup,
final response correction, and collapsed Citation excerpts. Use a real browser
for native dialog behavior, mobile
overflow, SPA refresh, security headers, and page-error/console cleanliness.
When native View Transitions are used, consume only the expected skipped
transition `AbortError`; do not leave its readiness promise unhandled or swallow
genuine update/navigation failures.

---

## Code Review Checklist

- Does any request or type expose a private/internal ID?
- Can a previous tenant's cached data survive logout or 401?
- Are loading, error, and empty states truthfully separated?
- Is every mutation protected by same-origin and CSRF behavior?
- Is polling bounded to nonterminal state?
- Is transcript text sourced from the transcript API?
- Are mobile controls labeled, focusable, and reachable at 390x844?
- Do OpenAPI JSON and generated TypeScript pass the stale check?
