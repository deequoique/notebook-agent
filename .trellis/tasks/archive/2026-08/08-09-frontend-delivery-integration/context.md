# Integration context

## 2026-08-09 baseline and ownership

- Integration owner: `/root` in
  `C:/Users/raede/.codex/worktrees/frontend-delivery-integration` on
  `codex/frontend-delivery-integration`.
- Base: collection handoff `5b8148521fc440c514a76a24317e65293b8069b9`,
  which is 17 commits ahead and 0 behind `upstream/main@a5d244e`.
- Showcase handoff: `codex/showcase-site@b123d0f`, ready for integration; its
  source worktree retains an unrelated uncommitted `uv.lock` that must not be
  staged or changed.
- Active Web handoff: thread `019fe22c-786a-70d1-9aea-8a57cb64c42b` owns
  `web-mvp-final` until it reports committed completion. Integration owner will
  not write there.
- Root `main` is not an integration surface: it contains untracked runtime,
  screenshots, output, and an old `web/` tree. Preserve all of it.
- Live processes: this task does not currently own a server. The collection
  preview on 5175 remains owned by the earlier collection task; the active Web
  thread owns any 5173 process it reports.

## 2026-08-09 delivery decision

- Repository evidence shows `web/` is already a standalone private application
  package: package manifest, lockfile, Vite build, tests, lint, typecheck, and
  committed OpenAPI schema all live under that boundary.
- It is not a reusable UI library because it owns routes, authentication,
  queries, CSRF, and product pages and has no second package consumer.
- Current security is intentionally same-origin. Relative `/api/v1` calls,
  exact Origin validation, `Sec-Fetch-Site: same-origin`, host-only `__Host-`
  cookies, and CSRF headers make direct cross-origin frontend/backend deployment
  unsupported.
- Official Vercel documentation confirms a `web/` monorepo Root Directory can
  be a separate project, SPA fallback can target `index.html`, and external
  rewrites can proxy the API without changing the browser URL. The concrete
  backend destination is deployment-specific and is not yet known, so it is not
  hardcoded in repository configuration.

## 2026-08-09 API-only implementation

- Added `WEB_SERVE_STATIC` with strict boolean parsing and default `true`.
- `build_web_app` now follows that setting only when tests/callers do not pass
  an explicit `mount_static` override. Existing bundled behavior is unchanged.
- `WEB_SERVE_STATIC=false` skips SPA mounting and does not inspect
  `WEB_STATIC_DIR`, allowing a backend image without `web/dist`.
- Documentation now covers bundled and split-service same-origin layouts,
  Vercel project boundaries, proxy/caching rules, verification, and rollback.
- Fresh focused verification: 16 tests passed across Web runtime/auth/CLI;
  `compileall app` and `git diff --check` passed.

## 2026-08-09 source integration and audit

- The active Web task finished cleanly and handed off commits
  `36d37d4c7ccf8d9d743070c5e798e2c84a578c21` and
  `0283811d4b184f4f80f634c96bc7b2055f5356b9`. They were integrated as
  `792b782` and `47c517d`; the focused 20-test frontend slice passed after the
  behavior merge.
- The Showcase source commit was already contained by the integrated history,
  so it was not cherry-picked a second time. Its unrelated source-worktree
  `uv.lock` remained untouched.
- Static review found no product use of unsafe HTML injection, browser-stored
  authentication tokens, console leakage, or a second package consumer that
  would justify extracting a reusable npm library. Large page components remain
  cohesive page-level owners; splitting them now would add indirection without
  a second responsibility or consumer.
- One deployment-documentation gap was fixed: when static assets are served by
  a separate frontend service, that service must reproduce the browser security
  headers previously added by the Python middleware. This includes CSP,
  anti-framing, MIME-sniffing, referrer, permissions, and HSTS policy.

## 2026-08-09 verification evidence

- Backend Web suite excluding the environment-gated PostgreSQL file:
  `90 passed, 9 skipped`. The two PostgreSQL cases were not run because this
  machine has no isolated `POSTGRES_PASSWORD`; the unconfigured run reported
  exactly those two fixture errors and no product-test failure.
- Frontend: 13 Vitest files / 59 tests passed; typecheck, ESLint, frozen OpenAPI
  contract check, and production Vite build all passed. The build emitted
  52.06 kB CSS (11.19 kB gzip) and 321.30 kB JavaScript (100.69 kB gzip).
- Python compilation passed and Alembic reported the single expected head
  `f6a7b8c9d0e1`.
- A task-owned Vite server ran only on `127.0.0.1:5176`. Desktop and exact
  390x844 browser smoke covered Showcase, login, an isolated logged-in library
  fixture, add dialog, and the long-title detail page. Evidence confirmed no
  horizontal overflow, three distinct loaded Showcase covers, configured login
  choices, correct per-region counts, collection filters, approximate progress,
  search suggestions, outside-click account-menu dismissal, tokenized URL
  input, vertically resizable notes, compact long-title layout, personalized
  descriptions, and an independently scrolling chapter region with separators.
  Browser logs contained only Vite connection and React development notices.
- The isolated browser fixture existed only inside the disposable verification
  tab; it did not alter repository files or production behavior. The tab was
  finalized and the owned 5176 process tree was stopped. Existing 5173/5175
  services were not inspected or changed.

## 2026-08-09 handoff decision

- The existing upstream PR is `deequoique/notebook-agent#2`, sourced from the
  user's fork branch `codex/web-video-library-mvp`. To avoid a duplicate PR, the
  verified integration head will fast-forward that fork branch and update the
  same PR. No merge is authorized or planned.

## 2026-08-09 final upstream refresh

- A final fetch found `upstream/main` had advanced from `a5d244e` to
  `3e8c2f8` through PR 3. The only product delta was an MCP worker readiness
  timeout increase from 0.35/0.75 seconds to 1/3 seconds; it was merged without
  conflict and did not touch the Web package or API contract.
- The upstream product change left one unit-test assertion at the old
  `timeout <= 0.35` boundary. With the required placeholder test environment,
  the MCP suite reproduced exactly that one failure. Updating the assertion to
  the new one-second inspect limit produced `18 passed`.

## 2026-08-09 fork and PR handoff

- Verified `origin/codex/web-video-library-mvp@f29b982` was an ancestor of the
  integration head, then fast-forwarded that fork branch to `ef9dec6` without
  rewriting history. Neither local nor remote `main` was updated.
- Updated the existing upstream PR 2 instead of opening a duplicate. It is open,
  non-draft, and mergeable, and explicitly states that maintainers must review
  and merge it. No merge action was performed.
- The only reported check failure is the external Vercel repository-owner
  authorization link. The PR records that as an environment/ownership gate,
  not as a passing deployment claim.

## 2026-08-09 final residual frontend pass

- Integration owner remains `/root` in this worktree. It is the only owner of
  Git integration, final validation processes, fork push, and PR updates.
- Residual product UI changes are owned by two source worktrees:
  `web-mvp-final` for brand/login/Showcase presentation and
  `why-saved-collections` for library progress and transcript reading.
- The source worktrees will receive narrow recovery commits before their new
  commits are integrated here one at a time. Their old histories must not be
  pushed over the existing PR branch because both are behind the current
  integration head.
- Root `main`, its untracked `web/` build/dependency tree, screenshots, runtime
  directories, and the Showcase worktree's unrelated `uv.lock` remain outside
  this delivery.
- The untracked `design-qa.md` contains machine-local absolute evidence paths
  and is not a portable repository artifact. Its durable conclusions are
  summarized here instead: the login background uses a generated raster
  halftone asset, retains card legibility, respects reduced motion, and passed
  desktop/mobile overflow checks in the producing task.
- Review removed one false-precision behavior before integration: subtitle
  links now retain backend-provided block timestamps and source URLs instead of
  inventing per-sentence times. The approximate queue progress remains clearly
  labeled as an estimate and resets when the visible work item set changes.
- No live server is currently owned by this pass. If a final browser smoke is
  required, `/root` will use a new task-owned loopback port and will not touch
  the existing 5173/5175 listeners.

## 2026-08-09 final residual integration and admission

- Source recovery commits were created without pushing their stale histories:
  `e4108cf4218fea78140d3c20991e8b530deb2525` for collection/progress/transcript
  truth boundaries and `f8295a710a784c5c6d868e8a9c9a3dada275071e` for shared brand/login/Showcase
  presentation. They were integrated here as `f239b12` and `a58d7d5`.
- Final product head is `09feca92ac16e6c8c0d61fc1863ea6ad86a99dd3`.
  It preserves outside-click account-menu dismissal, the independently
  scrolling keyboard-focusable chapter region, search suggestions, collection
  filters, and backend-provided transcript source URLs and timestamps.
- Review fixes removed persistent login-channel metadata, made Showcase
  evidence/reset controls immediately visible when rendered, added screen-reader
  text for moving content, and retained 44-by-44-pixel minimum targets for new
  language and transcript controls.
- The public Showcase no longer embeds long third-party transcript passages.
  Its moving preview now uses short original paraphrases labeled as "片段要点",
  while canonical video links, creators, titles, and timestamp evidence remain
  available for source verification.
- Fresh frontend admission on the final product head: 13 Vitest files / 67
  tests passed; frozen OpenAPI check, typecheck, ESLint, and production Vite
  build passed. The final build emitted 56.04 kB CSS (12.20 kB gzip) and 324.14
  kB JavaScript (102.39 kB gzip).
- The latest deployment-contract run passed 20 tests across the Linux templates,
  health boundary, Web CLI, and Web API composition. Alembic still reports the
  single expected head `a7b8c9d0e1f2`; `git diff --check` is clean.
- Independent code review reported APPROVE with no open P1/P2 correctness,
  security, state, routing, accessibility, mobile, or test-truth blocker. The
  architecture review classified the delivery as WATCH rather than BLOCK: the
  task record and public transcript-governance findings are closed here; the
  remaining non-blocking optimization is a smaller production derivative for
  the 291 kB, 1254-by-1254 brand PNG.
- Deployment preparation remains repository-only. No domestic server, DNS,
  certificate, firewall, production database, or production chat login was
  changed by this task. The Linux runbook and templates prepare an independently
  built `web/` package, same-origin `/api/` proxying, paired release directories,
  release-local migration admission, systemd units, Nginx security headers,
  immutable asset caching, rollback gates, and API-only mode. Real-host
  validation and deployment authorization remain external gates.
- Immediately before the handoff commit, a fresh fetch reported
  `upstream/main@6539f3b717fc928d787d748213a7c5f52b5e5b96`; the integration branch is
  32 commits ahead and 0 behind it. The existing fork PR branch remains an
  ancestor, 4 commits behind this product head, so the authorized update can be
  a normal fast-forward push with no history rewrite.

## 2026-08-09 final fork and PR refresh

- The fork branch `origin/codex/web-video-library-mvp` was verified as an
  ancestor and fast-forwarded from `988d064` to the record-backed admission
  head `da027c9`; no force push or `main` update was performed.
- Existing upstream PR 2 was updated in place rather than duplicated. It now
  distinguishes implemented product progress, the monorepo package decision,
  completed Linux/same-origin deployment preparation, fresh 67-frontend-test
  and 20-deployment-test evidence, and the exact external gates that remain.
- PR 2 remains open and intentionally unmerged. Its reported Vercel failure is
  the upstream repository-owner authorization link for a separate health-only
  project; the PR does not describe that status as proof of the prepared Linux
  deployment.
- This final record-only commit follows the product and admission commits. The
  durable `task.json.commit` intentionally continues to identify product head
  `09feca92ac16e6c8c0d61fc1863ea6ad86a99dd3` rather than self-referencing this
  later task-record commit.

## 2026-08-09 preview asset regression investigation

- The user-visible preview on `127.0.0.1:5175` was still owned by the earlier
  collection task. Process `79936` (parent `10236`) served
  `why-saved-collections/web/dist`, not this final integration worktree.
- That stale source worktree does not contain `BrandLogo.tsx`,
  `notebook-agent-logo.png`, or `login-halftone-wave.webp`. Browser inspection
  therefore found a text `N` mark, no image source, and `background-image: none`
  on both login-page pseudo-elements. The same stale build explains why the
  user's final Showcase changes appeared to have been discarded.
- The final product code and assets are present at product head `09feca92` and
  are already contained by the open fork PR branch. No Showcase recovery from
  deleted history is required; the preview needs to serve the final integrated
  build.
- Live-process handoff owner: `/root`. It will build
  `frontend-delivery-integration/web`, stop only the validated 5175 preview
  process tree, and restart the same root fixture against the final `web/dist`.
  Shared port: `5175`. Fixture:
  `C:/Users/raede/Desktop/dev/hackathon1/.runtime/authenticated_demo_server.py`.
  Log: `.runtime/frontend-preview-5175.log` in this worktree. Success requires
  HTTP 200, loaded logo assets on Library/Showcase/login, restored login
  halftone pseudo-elements, and final Showcase copy/structure. Stop on build,
  bind, HTTP, console, or asset failure.
- The final build succeeded and emitted the expected hashed logo and halftone
  files. After revalidating the old command line, `/root` stopped only the
  obsolete 5175 process tree. An initial restart attempt used the old wrapper
  name `preview_server.py`; that wrapper was no longer present and the process
  exited before binding, so no incorrect server remained live.
- The fixture already implements its own `main()` and static-file handler. It
  was then started directly against the final integration `web/dist`. The live
  listener is now process `25772` (launcher parent `23264`) on
  `127.0.0.1:5175`; its command line names
  `frontend-delivery-integration/web/dist` exactly.
- HTTP verification returned 200 for `/`, the 291,446-byte hashed logo, and the
  365,598-byte hashed login background. Static bundle inspection confirmed the
  final Showcase headline fragments, purpose copy, CTA, `片段要点` semantics,
  logo reference, login pseudo-element background reference, and Showcase
  brand styles.
- Browser automation had captured the stale-page failure before handoff: a
  text `SPAN` mark with `N`, no image source, and no pseudo-element background.
  The browser subsequently rejected an automated reload under its local-URL
  policy, so post-handoff visual confirmation remains a user-tab refresh step;
  the server, HTTP assets, compiled bundle, and source/test contracts are
  verified independently.
- After the handoff, the live server log recorded the current client loading
  `/library`, the final hashed CSS/JavaScript, the brand logo, and the login
  halftone asset with HTTP 200. `/favicon.svg` also returned 200 with
  `image/svg+xml`, and the document references it explicitly. This closes the
  reported Library/Showcase/login asset-loss symptom on the restored preview.
- The local fixture intentionally does not implement the real channel login
  challenge POST; clicking a login method therefore returns 404 in this visual
  preview. That is a fixture limitation, not evidence that the production auth
  routes or restored assets failed.
- Fresh focused regression verification on the final source tree passed 20
  tests across AppShell, LoginPage, ShowcasePage, and document-shell contracts.
  No redundant product change was added: the existing tests already require
  the shared brand image in Library/login and both brand images in Showcase;
  the reproduced defect came entirely from serving the stale worktree build.

## 2026-08-09 new upstream PR review

- The user requested a fresh review followed by a new pull request to upstream
  `main`. The existing PR 2 remains open and unmerged until maintainers decide
  its disposition; this pass will not close or merge it implicitly.
- Integration owner remains `/root` in this worktree. A fresh fetch reports
  `upstream/main@6539f3b717fc928d787d748213a7c5f52b5e5b96`; the current head contains
  that exact upstream tip and is 45 commits ahead, so no rebase is required.
- Two independent read-only lanes own code/security review and architecture
  review of `upstream/main...HEAD`. They may inspect completed output but may
  not edit files, change Git state, or run shared live processes.
- `/root` is the sole owner of the PR-admission test lane. Frontend OpenAPI,
  Vitest, typecheck, ESLint, and production build run serially in `web/`;
  backend Web/deployment/migration tests run from this worktree with its local
  `.venv`. Shared output is limited to `web/dist`, TypeScript build info, and
  pytest caches. No ports, databases, or the existing 5175 preview process are
  owned or changed by this validation.
- Success requires both independent reviews, all non-environment-gated checks,
  a clean diff, a new fork branch, and a new open non-auto-merged upstream PR.
  A repeated product failure blocks PR creation; unavailable PostgreSQL or
  external deployment credentials must be reported as explicit gates.

## 2026-08-09 fresh PR admission result

- The independent code/security lane ended `APPROVE`; the architecture lane
  ended `CLEAR`. All reported P1/P2 findings were fixed before Git delivery.
- Production no longer contains the local direct-login build flag or a
  synthetic authenticated session. Failed logout keeps the current private
  cache and session visible until the server confirms revocation.
- Saved notes now share one 500-character write contract across Web, Agent,
  MCP, ingestion, restore, and cross-channel identity merge. Existing legacy
  notes remain readable; a merge that cannot preserve both notes within the
  bound fails atomically instead of truncating data.
- Web, Agent, and MCP construct the ingestion submission service through one
  quota factory. Per-item bounds cover raw bytes, cue count, text length,
  segment count, and cumulative embedding input.
- YouTube metadata calls have a hard timeout. yt-dlp resolves the selected
  `json3` URL but no longer writes a provider-controlled subtitle file; an
  isolated HTTPS child checks Content-Length, reads only `max+1` bytes, writes
  no temporary file, and is terminated by the parent wall-clock timeout.
  A live public-video check returned 8,325 bytes and 61 cues through this path.
- Vercel health again requires the exact current Alembic revision
  `a7b8c9d0e1f2`; the deployment guide uses an explicit maintenance window
  rather than marking the incompatible `f6` schema ready.
- Fresh admission evidence: OpenAPI check, 74/74 Vitest tests, TypeScript,
  ESLint, Vite production build, 383 passed plus 8 environment skips in the
  non-external-database Python lane, `compileall`, one Alembic head, and clean
  whitespace validation.
- External gates remain: an isolated PostgreSQL upgrade/concurrency run, live
  Telegram and WeChat login approval, Redis/MinIO/Celery readiness, real
  domestic-host DNS/TLS/Nginx/systemd verification, and an authorized schema
  migration/deployment window. No production system was changed here.
- A new branch `codex/frontend-video-library-delivery` was created from the
  verified history. The old upstream PR 2 remains open and unmodified by this
  handoff; it will not be closed or merged implicitly.

## 2026-08-09 Git and PR handoff

- Product commit: `b28d6c1d96385c06ddcbf771e713ba2dca62dc83`
  (`Close the frontend delivery admission gaps`).
- Fork branch: `raederhans:codex/frontend-video-library-delivery`.
- New upstream review: https://github.com/deequoique/notebook-agent/pull/4
- The PR body separates completed code, independent frontend deployment
  preparation, and remaining real-environment gates. It explicitly records
  that no production deployment, database migration, PR closure, or automatic
  merge was performed.
