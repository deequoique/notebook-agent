# Implementation Plan

## Gate 0 - Planning, research, and branch safety

- [x] Read the linked conversation in full, including the final implementation
  checklist and earlier image references.
- [x] Fetch `origin` and `upstream`; confirm the fork has no unique commits and
  create `codex/web-video-library-mvp` directly from current `upstream/main`.
- [x] Audit existing ingestion, Agent, channel, tenant, migration, test, and
  deployment boundaries.
- [x] Gather pre-execution official FastAPI/Starlette/OWASP/SQLAlchemy evidence.
- [x] Gather official React/Vite/Router/Query/Tailwind/testing/accessibility
  evidence and record the Node/browser compatibility floor.
- [x] Complete architecture review; resolve any P1 objections before
  implementation.
- [x] Validate this complex Trellis task and start it only after PRD/design/plan
  review.

## Gate 1 - Fail-first schema and Web authentication

- [x] RED: add model/migration tests for challenge/session fields, public item
  IDs, archive state, indexes, and roundtrip/backfill.
- [x] RED: add framework-neutral auth tests for code hashing, TTL, attempt limit,
  channel binding, approval/exchange concurrency, expiry, disabled user,
  revocation, and no plaintext leakage.
- [x] GREEN: add models and additive Alembic migration.
- [x] GREEN: implement `UserScope` and Web auth service.
- [x] RED/GREEN: extend deterministic channel command parsing and handling for
  `/web-login CODE`; prove it never invokes the Agent and remains signed-gateway
  compatible.
- [x] RED/GREEN: create FastAPI app/dependencies/schemas with session cookies,
  CSRF + same-origin enforcement, security headers, safe error mapping, health,
  capabilities, and documented OpenAPI.

## Gate 2 - Fail-first library, submission, lifecycle, and management API

- [x] RED: tenant isolation for list/detail/dispatch; another tenant receives
  non-disclosing 404.
- [x] RED: search, lifecycle filters, sort, pagination, true-first-empty, and
  metadata skeleton behavior.
- [x] RED: batch add 1-10, required hashed/namespaced `Idempotency-Key`, ordered
  partial results, no remote fetch, same-user/item/key replay, and bounded
  ten-item broker failure.
- [x] RED: lifecycle matrix including pending + failed queue dispatch => failed,
  safe error allowlist, and derived actions.
- [x] RED: edit `why_saved`, archive, restore, and eligible retry; ensure no hard
  delete route exists and archived rows disappear from every Agent retrieval
  path.
- [x] GREEN: implement `ContentLibraryService` and authenticated routes using
  only server-derived `UserScope`.

## Gate 3 - Fail-first original transcript API

- [x] RED: prove transcript reads the MinIO raw JSON3 object rather than Segment.
- [x] RED: coalescing produces normalized chronological non-overlapping blocks,
  bounded pages/cursors, and timestamp links.
- [x] RED: missing key/object, corrupt JSON3, oversized object, wrong tenant, and
  store failure expose safe codes only.
- [x] GREEN: extend the raw object store with bounded reads and implement
  `TranscriptService` plus the detail/transcript endpoints.

## Gate 4 - Mobile-first React application

- [x] Add the minimal official-compatible React/TypeScript/Vite dependency set,
  generated OpenAPI TypeScript contract, scripts, lint/typecheck/test/build
  configuration, and same-origin development API proxy.
- [x] RED: API client/CSRF, auth state, lifecycle mapping, optional summary, and
  partial-result component tests.
- [x] GREEN: implement app shell and `/login` with all challenge states.
- [x] RED/GREEN: implement `/library`, search/filter/sort/page controls, first-use
  empty versus filter-empty, skeletons, and accessible native-dialog Add sheet.
- [x] RED/GREEN: implement `/videos/:id`, lifecycle, metadata, chapters,
  progressive transcript, `why_saved`, archive/restore/retry, and source links.
- [x] RED/GREEN: implement minimal account sheet/logout and responsive 390x844 /
  960-1120 layouts with reduced-motion/a11y behavior.
- [x] Update all `.trellis/spec/frontend/*.md` files in English to reflect the
  actual implemented conventions.

## Gate 5 - Integration, docs, and live verification

- [x] Update `.env.example` and deployment documentation for Web origin, cookie,
  session/challenge TTL, Web server, SPA build, reverse proxy/TLS, and safe
  startup/rollback. Do not expose the loopback gateway.
- [x] Run targeted Python RED/GREEN suites, then full `pytest -q`.
- [x] Run frontend lint, typecheck, unit tests, and production build.
- [ ] Run Alembic upgrade/current/check plus isolated downgrade/upgrade/backfill
  evidence when PostgreSQL is available.
- [x] Use the live-test ownership workflow for dev server and browser smoke;
  capture a 390x844 screenshot, console/network errors, responsive overflow,
  keyboard flow, and the core route journey.
- [x] Run `git diff --check` and Trellis validation.

## Gate 6 - Review and fork-only delivery

- [x] Perform correctness, security, accessibility, performance, and
  first-principles reviews; run a final official-guidance audit subagent.
- [x] Resolve all P1/P2 findings and rerun affected evidence.
- [x] Use the Lore commit workflow to create reviewable commits without `.omx/`.
- [x] Push only `codex/web-video-library-mvp` to `origin`.
- [x] Create a PR from the fork branch to `upstream/main`; do not update, merge,
  or push either repository's `main` branch.
- [x] Record the implementation commit and PR URL in task metadata.
- [ ] Finish/archive task records only when the real PostgreSQL migration run
  and live Telegram/WeChat acceptance criteria are genuinely complete.

## Browser-feedback closeout (2026-08-09)

- [x] Replace the Showcase hero orbit with three real YouTube covers in a wireframe 3D stack; verify all images load and have distinct browser `matrix3d` transforms.
- [x] Increase Showcase heading line/letter spacing, rebalance the purpose/process/demo/CTA copy, and add restrained visual separation to the three project-principle blocks.
- [x] Make overflowing VideoDetail chapters a bounded, divided, keyboard-scrollable region; verify `ArrowDown` changes the focused list's `scrollTop` in a 390×844 browser.
- [x] Run the final frontend suite: 12 test files / 42 tests, typecheck, lint, build, and OpenAPI stale check all pass.
- [x] Preserve the overall task in `review`; PostgreSQL migration and live Telegram/WeChat acceptance gaps above remain unchanged and are not implied complete by this UI closeout.

## Local demo-login closeout (2026-08-09)

- [x] RED: prove a demo-channel click bypasses the unavailable challenge API,
  shows an exit state, and opens the library.
- [x] RED: prove the selected demo channel is handed to the protected app as an
  in-memory session without browser-storage persistence.
- [x] GREEN: gate the shortcut behind the compile-time-only
  `VITE_LOCAL_DEMO_DIRECT_LOGIN=true` flag; preserve production authentication
  when the flag is absent.
- [x] RED/GREEN: keep both demo methods usable without requesting backend
  capabilities; retain server-controlled channel availability in production.
- [x] GREEN: add a moderate 420ms card exit transition and an 80ms
  reduced-motion path.
- [x] Rebuild the owned 5175 preview with the demo flag, run browser smoke for
  both channel rows, and complete final frontend validation.

## Unified route-transition closeout (2026-08-09)

- [x] RED: require Library card and login navigation to use the shared route
  transition, and lock the 220ms exit / 380ms enter CSS contract.
- [x] GREEN: route every user-triggered internal page change through one
  accessible Link/navigate wrapper without intercepting modifier clicks,
  external links, timestamps, or in-page anchors.
- [x] RED/GREEN: prove browsers without native View Transition support receive
  the CSS enter animation, and reduced-motion users receive no route animation.
- [x] Rebuild the owned 5175 demo preview and verify Library -> VideoDetail ->
  Library in the real browser with correct routes/headings and no console
  warnings/errors.
- [x] Run the final frontend suite: 14 test files / 74 tests, typecheck, lint,
  build, and OpenAPI stale check all pass.

## Validation Commands

```powershell
# Backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe check

# Frontend
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web test -- --run
pnpm --dir web build

# Repository
git diff --check
<bundled-python> ./.trellis/scripts/task.py validate 08-07-web-video-library-mvp
```

Environment-only gaps must be reported precisely; they do not authorize mock
production behavior, global config changes, credential changes, or touching
unrelated runtime state.
