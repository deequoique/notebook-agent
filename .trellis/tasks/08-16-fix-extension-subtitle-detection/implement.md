# 实施计划

## 1. Establish failing baselines

- [x] Make the extension test toolchain reproducible with the repository-declared runtime/package manager.
- [x] Add characterization tests for current YouTube, Kaltura and coordinator failure cases before changing behavior.
- [x] Record which tests fail against the current implementation and which gaps were previously untested.

## 2. Repair frame orchestration and failure semantics

- [x] Introduce a private structured attempt/error model without changing public `PageCapture` or `capture.v1`.
- [x] Make `captureActivePage` tolerate partial Frame errors, validate results and choose the best valid capture deterministically.
- [x] Preserve true no-caption behavior while surfacing discovered-but-unreadable subtitles as a safe retryable error.
- [x] Add direct orchestration tests for supported/unsupported routes, top/all frames, empty/partial/multiple results and stable errors.

## 3. Repair YouTube adapter

- [x] Resolve current player response from tested sources and reject stale video IDs after SPA navigation.
- [x] Prefer current/default caption tracks, then use manual-caption-first selection with ASR fallback, and normalize JSON3 cues defensively.
- [x] Fall back from a successful-but-empty/non-JSON JSON3 response to WebVTT and the original YouTube XML on the same trusted signed caption endpoint.
- [x] Fall back from empty timed-text representations to the official same-origin transcript endpoint, after decoding its opaque params and requiring the current video ID.
- [x] Add fixtures/tests for each response source, track choice, no-caption, malformed response, stale response and fetch/parse failures.

## 4. Repair NTULearn/Kaltura adapter

- [x] Expand caption discovery across native TextTrack, DOM track, trusted resource and available player track descriptors.
- [x] Preserve TextTrack modes, continue after individual candidate failures and retain deterministic language/title/canonical URL behavior.
- [x] Harden VTT/SRT/TTML parsing and trusted URL checks.
- [x] Add fixtures/tests for outer shell vs player frame, every discovery path and format, no-caption, fallback, invalid timing/host and secret stripping.
- [x] Add the live-proven `ntulearnv1.ntu.edu.sg` media origin to the exact manifest/audit allowlist, extension routing and backend capture URL contract; add positive tests plus wildcard/nearby-host rejection tests.

## 5. Verification and packaging

- [x] Run extension unit tests with coverage, strict typecheck, ESLint, production build and package audit.
- [x] Run focused backend browser-capture contract/API tests to prove protocol compatibility.
- [x] Add mutually exclusive production/local extension build targets; resolve the API origin only from the selected exact manifest permission and audit both variants.
- [x] Bound extension API requests to 10 seconds with safe network/timeout/configuration errors and direct unit tests.
- [x] Bind pairing/grant state to the selected API origin; reset cross-target state while adopting legacy state only on the original production target.
- [x] Start the local HTTPS Web stack plus Mailpit/Redis/MinIO dependencies and prove a synthetic extension-origin pairing returns a local approval URL.
- [x] Build the unpacked extension and select 5 distinct captioned YouTube videos covering manual/ASR captions, 2+ languages and one same-tab SPA navigation.
- [x] Run the real YouTube matrix and require `5/5` successful recognitions; safe numeric metrics were reproduced from the same public resources, while non-persisted latency/text evidence is documented as a user-accepted closure waiver.
- [x] Select 2 different, authorized, captioned NTULearn/Kaltura entry IDs, covering outer-media/embed entry types where available.
- [x] Run the authenticated Kaltura matrix and require `2/2` successful recognitions; both distinct entries reached `ready/completed` with redacted identifiers and numeric metrics.
- [x] Review all seven real-video records for stale media identity, incomplete cue ranges and leaked KS/signature/cookie/auth material.
- [x] Persist the completed matrix in `real-video-validation.md`; fixtures or repeated URLs do not count toward the 5+2 threshold.
- [ ] Reload the final local `extension/dist` build before continuing the 5+2 matrix; an older production-target build does not count.
- [x] Review the final diff for unrelated dirty-worktree changes and report only files attributable to this task.

## 6. Finish gates

- [x] Run Trellis check/review against PRD and design.
- [x] Update `.trellis/spec/backend/browser-companion-capture.md` only for durable new behavior or test requirements learned from the fix.
- [ ] Commit the task-scoped changes, record validation evidence, and archive/finish the Trellis task.

## Rollback points

- After step 2: coordinator changes can be reverted independently while retaining characterization tests.
- After steps 3/4: each platform adapter can be reverted independently behind the unchanged public protocol.
- Packaging/version changes happen only after all gates pass.

## Implementation evidence (2026-08-16)

- `extension/src/page-capture.ts` now returns private structured attempts from page-world adapters, allowlist-rebuilds normalized results before aggregation, preserves no-caption versus caption-read failures, and keeps public `PageCapture`/`capture.v1` unchanged.
- The review aligned Kaltura IDs with the backend's numeric-prefix contract, removed `entry_id`/`uiconf_id` false positives, honored YouTube's current/default caption track before manual/ASR fallback, rejected untrusted redirect targets, ignored non-caption TextTracks, and made equal-score selection deterministic.
- The live-proven `ntulearnv1.ntu.edu.sg` origin is now an exact host in the extension manifest/audit allowlist, platform router, Kaltura frame/caption-resource trust, extension `page_url` handling, and backend page-URL contract. The canonical reference host set remains unchanged. Tests cover its native TextTrack/resource paths and reject canonical drift plus nearby/wildcard hosts.
- `extension/src/page-capture.test.ts` covers coordinator routing/partial frames, current/default YouTube track selection, config/initial responses, stale SPA identity, manual-to-ASR fallback, native TextTrack/DOM track/performance/player descriptors, JSON3/VTT/SRT/TTML parsing, no-caption versus read/parse failures, trusted-host/redirect filtering, and allowlist removal of secret-bearing extras.
- Deterministic checks completed with the bundled Node runtime: 49 extension tests passed; strict TypeScript passed for production and test/config sources; ESLint, production build and exact permission/artifact audit passed.
- Function-to-string evaluation of both MAIN-world injected adapters passed, covering the MV3 serialization boundary at unit level.
- `pnpm test:coverage` passed its core `src/page-capture.ts` thresholds (statements 80%, branches 72%, functions 85%, lines 90%); observed coverage was 83.33% / 75.26% / 88.57% / 91.33% respectively.
- Focused backend capture/pairing/API regression completed with `NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false`: 22 tests passed. The explicit override neutralized an unrelated developer-shell logging flag; without it, settings validation fails before the target assertion.
- The 5 YouTube + 2 authenticated NTULearn/Kaltura real-video qualification matrix remains pending and is still a hard completion blocker.
- Browser qualification evidence: isolated Chrome loaded and executed the final unpacked extension but YouTube served empty timed-text bodies to that automation profile; connected user Chrome isolates MAIN-world player APIs, and browser policy blocked automated private Kaltura media navigation. The required continuation is recorded in `real-video-validation.md` without private titles, URLs or identifiers.
- After user-prepared live tabs became available, the actual top-level Kaltura media origin was observed as `ntulearnv1.ntu.edu.sg`, with a top-level video and an English subtitle TextTrack. The exact-origin support is now implemented and covered by deterministic tests; the 2-video live requalification remains required before closing the task.
- The extension now has audited `production` and `local` build targets. Each artifact contains exactly one Notebook Agent API host permission, the worker resolves only that exact permission, and API requests abort after 10 seconds with a stable popup error. The final `extension/dist` is the local variant targeting `http://127.0.0.1:8000`; the approval Web UI remains `https://localhost:8443`.
- The local stack returned HTTP 201 for a synthetic fixed-extension-origin pairing and produced an approval URL on `https://localhost:8443/account/browser-companion`; the warmed request completed in about 1.4 seconds. The production endpoint timeout no longer affects the local build after Chrome reloads it.
- The initial HTTPS API target was rejected by the extension before HTTP because the local Caddy issuer was not trusted by the extension service worker. The local API target now uses exact loopback HTTP while keeping the approval UI on HTTPS, avoiding both the certificate exception and any production fallback.
- A real user Chrome page exposed three manual YouTube caption tracks and had CC enabled with a visible caption segment, while the extension returned `caption_fetch_failed`. The adapter previously forced only `fmt=json3` and classified an empty/non-JSON body as a fetch failure. It now retries WebVTT and original YouTube XML without persisting or returning the signed URL. Two direct regression tests cover VTT and XML success.
- After the YouTube fallback change, 68 extension tests passed. Core adapter coverage remained above thresholds at 83.17% statements, 75.43% branches, 90% functions and 90.43% lines; strict TypeScript, ESLint, production package audit and final local package audit passed.
- A second real YouTube page also failed after all three timed-text representations were empty. Its public page data contained a current-video transcript endpoint and complete InnerTube client context; an anonymous probe correctly failed closed with `FAILED_PRECONDITION`, proving that real qualification must use the user-invoked browser session. The adapter now uses that endpoint only after its base64url params decode to the current video ID.
- After the official transcript fallback, 70 extension tests passed. Core adapter coverage was 83.69% statements, 76.15% branches, 90.4% functions and 90.4% lines; strict TypeScript, ESLint, production package audit and final local package audit passed.
- Live Kaltura inspection showed `serveWebVTT` HLS playlists with relative `segmentIndex/*.vtt` resources. The adapter now rejects CSS/JS pseudo-resources, requires bounded VOD playlists, validates trusted relative fragments, applies `X-TIMESTAMP-MAP` MPEGTS/90000 offsets, merges/deduplicates cues, and fails closed on partial/oversized/stalled playlists without exposing signed URLs.
- Independent review hardened the Kaltura playlist repair: finite playlists now outrank buffered native cues, standalone/member segments cannot become partial fallback captures, valid empty WebVTT segments are accepted, malformed timestamp maps fail closed, request/body timeouts abort, and aggregate segment bytes are bounded. `X-TIMESTAMP-MAP` accepts either attribute order while enforcing the 33-bit MPEGTS range.
- After that review, 90 extension tests passed. Core adapter coverage was 86.13% statements, 78.34% branches, 91.50% functions and 92.38% lines; strict TypeScript, ESLint, production build/audit and final local build/audit passed. The final `extension/dist` remains the audited local variant.
- Final live qualification reached YouTube `5/5` and authenticated NTULearn/Kaltura `2/2`. The two selected Kaltura captures contain 3,596 and 2,631 cues with 99.85% and 100.00% temporal coverage; both items and dispatches converged to `ready/completed`. Public YouTube cue/time metrics were safely reproduced from the same caption resources because popup latency/text telemetry is intentionally not persisted. The user explicitly accepted the documented latency and second-language evidence waivers before closure.
- Multi-hour capture ingestion no longer loops on a one-cue hard-cut window, skips per-cue semantic embedding above 512 cues, and reuses the latest tenant-matching ready capture on retry even after the original dispatch fails. A 3,596-cue capture now produces 224 searchable segments in about 74 seconds.
- Publisher acknowledgement paths now lock and re-check pending dispatches before writing enqueued/failed, so a fast worker's running/completed state cannot be overwritten. Existing ready/enqueued live-validation rows were safely reconciled to completed.
- Final verification: extension 98/98 with 85.79% statements, 77.96% branches, 90.85% functions and 91.97% lines; extension strict TypeScript/ESLint and production/local package audits passed; Web 112/112 with TypeScript/ESLint/build passed; task-focused backend 119/119 passed; Trellis validation and `git diff --check` passed. The clean-environment Python repository suite has two pre-existing baseline failures because `docs/deployment.md` and `docs/frontend-deployment.md` are absent from HEAD; all 65 tests in those previously failing modules pass except the two missing-document assertions.
- The downloadable local `0.1.3` zip was rebuilt from the final audited local `extension/dist`, contains `api-client.js` plus the repaired 74 KB capture adapter, and has only the exact loopback Notebook Agent API permission.
