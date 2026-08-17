# Context

## 2026-08-09

- Owner: `/root` (integration owner and sole live-process owner for this task).
- Source requirement: browser comment on the Add Video dialog plus follow-up to
  check upstream and prefer reuse of `why_saved`.
- Upstream evidence: `upstream/main@a5d244e` has no Web folder/collection API;
  its `ContentItem.tags` is connector metadata and its management service limits
  `why_saved` to 500 characters.
- Isolation: implementation worktree is
  `C:/Users/raede/.codex/worktrees/why-saved-collections` on
  `codex/why-saved-collections`, based on Web MVP HEAD `f29b982`.
- Existing live previews remain owned by `/root`: Showcase on 5173 and the
  authenticated Web fixture on 5174. This task will use a separate port before
  changing either preview.
- TDD evidence: pure parser, add-dialog, library filter, card, and detail tests
  all failed for the intended missing behavior before the minimal implementation;
  the focused suite now passes 30 tests.
- Live validation owner: `/root` only. Commands run serially from `web/`:
  `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm check:api`, and `pnpm build`.
  Build output is isolated to this worktree's `web/dist`.
- Preview plan: port 5175 only, with logs and PID under this worktree's
  `.runtime/why-saved-collections/`. Ports 5173 and 5174 remain untouched.
- Success: all frontend checks exit 0 and desktop plus 390x844 browser smoke
  show no horizontal overflow. Stop on a repeatable product failure; retry at
  most after collecting new evidence.
- Final verification: 13 frontend test files / 51 tests passed; TypeScript,
  ESLint, OpenAPI stale check, Vite production build, and `git diff --check`
  passed. The OpenAPI check reused the project virtual environment with a
  one-command `PYTHONPATH` pointed at this worktree; no API files changed.
- Browser evidence: desktop filtering reduced `#产品调研` to one item while
  retaining the other chips; the Add Video dialog exposed all discovered
  collections, inline invalid-name guidance, a selected new collection, and a
  vertically resizable reason area. Detail displayed `#产品调研` separately and
  the editor contained only human-readable reason text. A calibrated 391x844
  viewport (the browser's nearest integer representation of 390x844) had no
  horizontal overflow; the bottom-sheet dialog stayed within the viewport and
  became internally scrollable.
- Preview: `http://127.0.0.1:5175/library`, local fixture only. Listener PID
  observed as 47536 (venv launcher PID 72196); logs are under
  `.runtime/why-saved-collections/`. Leave running for user review.
- Remaining integration boundary: commit this isolated branch, then integrate
  it into `codex/web-video-library-mvp` only after that shared worktree's
  unrelated Showcase/chapter WIP is cleanly separated. Do not auto-merge.

## 2026-08-09 compact URL follow-up

- Owner remains `/root`; no other agent may run or interpret this task's build,
  browser smoke, port 5175, logs, or `web/dist` output.
- User feedback requested a smaller URL input whose height follows real content
  and confirmed URLs become visually distinct removable tags.
- TDD RED proved the old `rows=6` textarea lacked the compact/tag behavior;
  focused GREEN is 9/9 Add Video dialog tests.
- Final commands run serially from `web/`: full Vitest, typecheck, ESLint,
  OpenAPI stale check, production build, and diff check. Then only the existing
  5175 fixture is restarted and checked at desktop plus calibrated mobile size.
- Success: confirmed links wrap into distinct tags, removal and 10-item count
  work, the input and dialog grow without horizontal overflow, and submission
  remains `{urls, why_saved}`. Stop on a repeatable failure before committing.
- Final follow-up verification: 13 test files / 52 tests passed; TypeScript,
  ESLint, OpenAPI stale check, Vite build, and diff check passed. Desktop browser
  measurements were approximately 41px initial input height, 97px for a long
  wrapped draft, and 193px for three confirmed tags. Individual removal updated
  the list and `2 / 10` count. At the calibrated 391x844 viewport, two tags grew
  the token region to about 149px, the dialog stayed within the viewport, its
  form became internally scrollable, and neither page nor token region overflowed
  horizontally. Port 5175 remains live with the refreshed build and two sample
  URL tags visible for review.

## 2026-08-09 account-menu click-away follow-up

- Owner remained `/root`; full checks and the existing 5175 preview were run
  serially without touching 5173 or 5174.
- TDD RED showed the native details element stayed open after clicking a
  control outside the header menu. The minimal implementation retains native
  summary toggling and removes `open` only for document pointerdown events whose
  target lies outside the menu ref.
- Final verification: 13 test files / 53 tests passed; TypeScript, ESLint,
  OpenAPI stale check, Vite build, and diff check passed. In the refreshed
  browser, opening the menu set `open`, clicking the Telegram text inside kept
  it open, and clicking the library heading removed `open`. The 5175 deliverable
  tab is left open with the account popover expanded for direct user review.

## 2026-08-09 processing-area hierarchy follow-up

- Owner remained `/root`; full checks, the existing 5175 preview, and its
  viewport override were handled serially without touching 5173 or 5174.
- TDD RED proved the old library lacked separate readable/work regions. The
  minimal implementation groups queued, processing, action-required, and
  failed items in a lower `整理队列`, while ready items remain above and move
  there automatically through the existing four-second query polling.
- The progress indicator is deliberately approximate: queued 20%, processing
  65%, and terminal action-required/failed states 100%, averaged and rounded.
  It describes the current workflow stage, not a promise of successful output.
- Final verification: 13 frontend test files / 54 tests passed; TypeScript,
  ESLint, OpenAPI stale check, and the Vite production build passed. Desktop
  browser evidence showed four ready cards, two work cards, a 77px separation,
  a fine solid divider, progress 83%, and no horizontal overflow.
- The browser was calibrated to an actual 391x844 viewport for the narrow
  smoke: both regions remained ordered, the queue header stayed inside its
  339px content width, progress remained 83%, and no horizontal overflow was
  present. The viewport override was reset afterward and the 5175 deliverable
  returned to desktop sizing for user review.

## 2026-08-09 queue-copy removal follow-up

- User selected the explanatory sentence under `整理队列` and requested its
  removal. TDD RED confirmed the sentence remained in the rendered region;
  GREEN removes the node and its dedicated style while preserving grouping,
  polling, and the semantic progress element.
- Final verification: 13 frontend test files / 54 tests passed; TypeScript,
  ESLint, OpenAPI stale check, and the Vite production build passed. The
  refreshed 5175 browser showed the copy absent, a 16px title-to-grid gap,
  progress still present, and no horizontal overflow.

## 2026-08-09 local search suggestions follow-up

- User feedback requested immediate matching against existing targets such as
  authors and titles while typing. The implementation derives suggestions only
  from the authenticated library response already in memory: title, author,
  parsed collection tag, and ordinary save reason. It caps output at six and
  sends no request until a suggestion or explicit query is submitted.
- TDD RED showed the previous search rendered no suggestion list. The first
  browser pass then exposed a pointer-ordering bug: input blur unmounted a
  suggestion before its click could apply. A regression test now requires the
  suggestion mouse-down to prevent that blur. A second browser pass proved an
  outside click should close the list; document pointer handling plus input
  click reopening now cover that case without changing server search behavior.
- Final verification: 13 frontend test files / 55 tests passed; TypeScript,
  ESLint, OpenAPI stale check, and the Vite production build passed. Desktop
  browser smoke showed three `How` title matches, a single `David Kelley · TED`
  author match, successful suggestion submission to one visible card, outside
  dismissal, and click-to-reopen. At 391x844 the 357px suggestion panel stayed
  within the viewport and the page had no horizontal overflow.
- Port 5175 remains live and the desktop preview is left with `How` suggestions
  open for direct review. Ports 5173 and 5174 were not touched.

## 2026-08-09 long detail-title balance follow-up

- User feedback identified `How to practice effectively...for just about
  anything` as a title that visually overwhelmed its cover. The fix preserves
  the complete source title, uses a compact density after 44 visual character
  units (CJK characters count as two), and changes the desktop hero from a 43%
  media column to a balanced 50% media column.
- TDD RED showed the reported title had no compact-density contract. GREEN adds
  the density marker and responsive type rules without truncation. A normal
  `How to Talk to Users` title remains standard density at 64px.
- Final verification: 13 frontend test files / 56 tests passed; TypeScript,
  ESLint, OpenAPI stale check, and Vite build passed. At 1724x1375 the reported
  cover measured 460x259 and the compact 52px title measured 436x204, with no
  horizontal overflow. At 391x844 the cover measured 339x191, the title used
  32px type, and no horizontal overflow appeared. The 5175 preview is restored
  to the reported desktop detail for review; ports 5173 and 5174 were untouched.

## 2026-08-09 add-dialog note label follow-up

- User selected `为什么保存（可选）` in the Add Video dialog and requested the
  shorter utility label `备注`. TDD RED produced four expected dialog failures;
  the single production-copy replacement returned all nine focused tests to
  green without changing the internal `why-saved` field or submission payload.
- Final verification: 13 frontend test files / 56 tests passed; TypeScript,
  ESLint, and Vite build passed. The refreshed 5175 dialog reports
  `备注（可选）`, retains field name `why-saved`, remains open for review, and
  has no horizontal overflow. Ports 5173 and 5174 were untouched.

## 2026-08-09 personalized demo descriptions follow-up

- Root cause: 5175 imports the ignored shared
  `.runtime/authenticated_demo_server.py`, whose `_item` helper assigns one
  generic description to every video. This is demo data, not a failed production
  crawl. Production `YouTubeConnector.fetch_meta` reads `description`,
  `process_item` writes it to `ContentItem.description`, and the model already
  persists the field.
- Public metadata was freshly retrieved with the repository's `yt-dlp` runtime
  for `aircAruvnKk`, `f2O6mQkFiiw`, `UF8uR6Z6KLc`, `PZ7lDrwYdZc`, and
  `16p9YRF0l-g`. All five returned non-empty official descriptions. The 5175-only
  wrapper now supplies grounded Chinese descriptions covering neural-network
  layers and parameters, deliberate practice, Jobs's three stories, Atomic
  Habits' four laws, and Kelley's creative-confidence argument.
- TDD stayed inside the ignored runtime boundary: the first assertion failed
  with zero overrides; GREEN requires exactly five unique descriptions, at
  least 60 characters each, and rejects the generic placeholder. The check
  passes, `py_compile` passes, and the runtime files remain excluded from Git.
- The owned 5175 process was restarted without touching 5173 or 5174. All five
  detail APIs returned unique descriptions of 96-115 characters. Browser smoke
  visited every detail with no desktop overflow; at 391x844 the longest checked
  description occupied 339x168 with no horizontal overflow. The preview is left
  on `/videos/creative-confidence` for user review.

## 2026-08-09 region count follow-up

- User feedback identified the primary `6 个视频` label as a placeholder-like
  mismatch: the API total covered four readable cards plus two queue cards,
  while the label sat directly above only the readable region.
- TDD RED found no `当前可阅读视频数量` label and showed the old value as 3 for
  a fixture with one readable and two work items. GREEN binds the two labels to
  the already-grouped `readableItems.length` and `workItems.length` values.
- Final verification: 13 frontend test files / 56 tests passed; TypeScript,
  ESLint, OpenAPI stale check, and Vite build passed. Desktop browser evidence
  showed four readable cards with `4 个视频` and two queue cards with `2 个视频`.
  Selecting `产品调研` reduced the readable region and its count to one while
  the empty queue region disappeared. Narrow smoke had no horizontal overflow,
  and the 5175 preview was restored to the unfiltered desktop library.
