# Live validation ownership

- Owner: `/root` (primary integration agent). No subagent may start, poll,
  retry, or stop the owned full-suite, build, migration, server, or browser
  processes.
- Integration repository:
  `C:\Users\raede\.codex\worktrees\frontend-delivery-integration` on
  `codex/frontend-delivery-integration`.
- Full Python verification uses the project virtual environment with an
  explicit port-1 PostgreSQL URL (`connect_timeout=1`) and a dummy OpenAI key.
  Database-gated tests therefore skip explicitly instead of hanging or
  connecting to an unknown database.
- Frontend verification uses the pinned pnpm version in `web/`; the owned
  commands are OpenAPI contract check, lint, Vitest, typecheck, and production
  build.
- Migration verification uses Alembic graph inspection and offline upgrade
  SQL. Docker, PostgreSQL executables, and a dedicated database are absent on
  this host, so a real upgrade/downgrade/upgrade remains an explicit CI or test
  database gate.
- Earlier browser smoke used temporary loopback fixtures that were stopped
  after evidence capture. The newer user-review preview is documented in the
  local demo-login section below and intentionally remains on port 5175.
- Stop conditions: verification complete; an unrecoverable missing runtime; or
  the same failure three times under the same hypothesis.

## Local demo-login closeout (2026-08-09)

> **Superseded later on 2026-08-09:** the final production delivery removed
> `VITE_LOCAL_DEMO_DIRECT_LOGIN`, the synthetic authenticated session, and the
> direct-login transition described in this historical section. The login page
> now exposes only channels enabled by the server capabilities endpoint and
> always uses the real challenge/session exchange. The `.runtime` fixture may
> still serve static authenticated visual data locally, but it is not a product
> authentication path and is excluded from version control.

- The login screen keeps the production channel challenge flow by default.
  Only a build with `VITE_LOCAL_DEMO_DIRECT_LOGIN=true` enables the local demo
  shortcut requested while the real auth backend is unavailable.
- Demo mode does not request the backend capabilities endpoint; both channel
  rows are immediately usable. Production mode still derives availability
  exclusively from server capabilities.
- In demo mode, selecting WeChat or Telegram creates an in-memory React Query
  session for the selected channel, waits for a 420ms card exit transition
  (80ms under reduced motion), and opens `/library`. It writes no auth data to
  cookies, `localStorage`, or `sessionStorage`.
- `/root` owns the local authenticated fixture and static preview on port 5175.
  The fixture is
  `C:\Users\raede\Desktop\dev\hackathon1\.runtime\authenticated_demo_server.py`;
  stdout/stderr logs are under this worktree's `.runtime/` directory. Only this
  owner may rebuild `web/dist`, restart the listener, or perform browser smoke.
- TDD evidence: the direct route and transition tests failed first against the
  previous implementation; after the minimal implementation, LoginPage,
  document-shell, and private-cache focused suites pass (23 tests).
- Final frontend evidence: OpenAPI stale check, ESLint, TypeScript, the default
  production build, and the demo-flag build all pass; Vitest reports 13 files
  and 71 tests passed. Browser smoke on the owned 5175 fixture verified both
  Telegram and WeChat transitions, the `/library` destination and heading, no
  horizontal overflow, and no console warning/error. The verified Library tab
  remains open for user review.

## Current integration evidence

- The feature branch is based on refreshed `upstream/main` at `6539f3b`. The
  integration preserves upstream item-management tools, Composer and
  diagnostics behavior, channel identity linking, diversified retrieval,
  Vercel static-boundary rules, and Neon deployment documentation.
- Web archive and Agent recycle-bin semantics are reconciled: `archived_at` is
  a reversible Web-only archive, `deleted_at` is the recycle bin, deletion wins
  when both exist, Agent reads exclude both, and restore/re-save clears both.
- Ingestion retry remains tenant-bound, quota-bound, budget-bound, and durable.
  Queue publication failure now converges the item and dispatch atomically even
  when a concurrent delete wins the race. Internal item-management statuses are
  mapped to the stable public Web batch contract instead of causing a 500.
- Alembic has one head, `f6a7b8c9d0e1`, which is a no-op merge revision over
  the Web branch (`d3f4a5b6c7d8`) and upstream MCP branch
  (`d4e5f6a7b8c9` -> `e5f6a7b8c9d0`).
  `alembic upgrade head --sql` succeeds. Offline downgrade intentionally cannot
  cross the published item-management revision because that downgrade performs
  a live deleted-row safety check; the published revision was not rewritten.
- Final post-MCP backend verification passed in one run: `331 passed, 58
  skipped in 167.67s`. All skips are PostgreSQL-gated under the explicit
  port-1 URL. The frozen lock now includes both MCP and Web/FastAPI runtime
  dependencies, and the Windows stdio protocol test uses explicit UTF-8 plus a
  cross-platform pipe timeout.
- Final frontend verification passed: OpenAPI JSON and generated TypeScript
  contract were unchanged, ESLint passed, all 39 Vitest cases across 12 files
  passed, TypeScript typecheck passed, and the Vite production build completed
  (`307.38 kB` JavaScript, `96.65 kB` gzip).
- The new upstream environment guide now includes a first-class same-origin
  Web profile, the complete `WEB_*` variable reference, frozen frontend build
  commands, login-channel dependency, port separation from MCP, and the
  browser/API smoke sequence.
- Earlier Chromium smoke verified the private login/library/detail journey and
  the public Showcase at desktop and 390x844 widths, including direct-route
  refresh, source timestamp links, no horizontal overflow, and clean console
  output. The latest integration did not change those UI files.
- Correctness/security reviewers report no open P0-P2 blocker. Remaining
  non-blocking risks are the unavailable real PostgreSQL migration/concurrency
  run, deliberate duplication between the two retry admission surfaces, and
  Windows file-log privacy depending on the deployment directory's NTFS ACL.
- One stale PostgreSQL test process tree left by a completed agent was detected
  before the final suite and stopped by exact PID. No owned pytest or browser
  automation remains after validation; the reviewed 5175 preview intentionally
  remains available for the user.

## Delivery status

- Pushed only `codex/web-video-library-mvp` to the user's `origin` fork.
- Opened ready-for-review PR
  `https://github.com/deequoique/notebook-agent/pull/2` from
  `raederhans:codex/web-video-library-mvp` to
  `deequoique/notebook-agent:main`.
- Neither repository's `main` branch was pushed or modified, and the PR remains
  unmerged for source-maintainer approval.
- Keep this task in `review` after PR creation; do not archive it while the real
  PostgreSQL migration roundtrip and manual Telegram/WeChat acceptance remain
  outstanding.

## Unified route-transition closeout (2026-08-09)

- All user-triggered internal page changes now share one route-transition
  boundary: Library cards, VideoDetail back navigation, the protected wordmark,
  Showcase login calls-to-action, login success, logout, and unauthorized
  session exit. In-page anchors, YouTube links, timestamp links, and forms keep
  their native behavior.
- Supporting browsers use the native View Transition API: page content exits in
  220ms and enters in 380ms while the protected top bar stays stable. Browsers
  without that API use a 380ms CSS enter animation; reduced-motion preference
  bypasses both paths.
- TDD evidence: the first route-link, login, and CSS contract run failed with 3
  failures / 22 passes; the no-native-API compatibility tests then failed with
  2 failures / 8 passes. Both slices passed after their minimal implementations.
- Final frontend evidence: OpenAPI stale check, ESLint, TypeScript, Vitest (14
  files / 74 tests), and production plus demo-flag builds pass. Browser smoke on
  the owned 5175 fixture verified Library -> VideoDetail -> Library in the
  current no-native-API browser, correct URLs/headings, and zero console
  warnings/errors. The verified VideoDetail tab remains open for review.
