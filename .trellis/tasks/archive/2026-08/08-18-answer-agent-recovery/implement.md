# Implementation Plan

- [x] Add deterministic tests for primary failure with accumulated evidence, three answer
   attempts, multi-item allocation, exact URL retention and exhausted failure behavior.
- [x] Extend the answer-only structured contract and server validator for selected-item
   coverage, exact-scope retention, five-item and eight-segment limits.
- [x] Refactor AnswerPipeline to expose one common answer-recovery method with exactly
   three total attempts and no deterministic evidence fallback.
- [x] Route every existing primary/answer failure-with-evidence path through that method;
   preserve no-evidence and terminal-action behavior.
- [x] Remove obsolete fallback text/paths, align persistence and MCP citation projection,
   and add safe attempt diagnostics.
- [x] Run focused agent/MCP tests, then broader isolation/action/conversation/evaluator
   regressions. Independently review safety boundaries and retry accounting.
   The final focused recovery/schema-feedback suite passed 160 tests;
   compilation and `git diff --check` also pass. The full suite currently
   reports 646 passed, 73 skipped, and 6 unrelated environment/configuration
   failures.
- [x] Replay `human.he-001` once with the real model, export the answer as pending human
   review, and verify public status/error/citation projection without assigning verdict.
   Post-refactor run `20260819T072208Z-7b07bfe7` / request
   `b19dde9f64274209b0108a6966cac7ca` returned `status=ok` with no error code and
   eight citations; all citations belonged to item `153`. The primary diagnostic
   was a timeout and answer recovery succeeded. The workbook remains
   `pending_review` for human-only quality judgment.

## Top-level selection follow-up

- [x] Add top-level `selected_segment_ids` with schema `maxItems: 8`.
- [x] Require section citation IDs to exactly consume the top-level selection while
      preserving candidate, five-video and explicit-URL scope checks.
- [x] Run only focused schema/recovery/MCP tests for this follow-up (`111 passed`).
- [x] After focused tests pass, run exactly one paid `human.he-001` export.
      Post-refactor run `20260819T072208Z-7b07bfe7` remains `pending_review`;
      no automated verdict is assigned.

## Rollback points

- Structured output/schema change can be reverted independently before orchestrator
  routing changes.
- Recovery routing must not ship unless exhausted attempts return a typed failure and
  all exact-scope/tenant regressions pass.
- No configuration limits or database schema are changed.

## Verification snapshot

- Answer recovery is bounded to three total tool-free model attempts. Invalid
  output, timeout, usage limit, provider failure, and runtime failure consume
  one attempt; exhaustion returns `failed/answer_unavailable` with empty
  Citations and no persisted transient turn.
- Explicit URL scope, selected-item coverage, the five-item limit, and the
  eight-segment limit are enforced by the server. A successful answer uses the
  same validated Citation selection for visible sources, persistence, and MCP.
- Recovery diagnostics retain only safe class/category/attempt/status fields;
  provider payloads are not logged in answer-stage retries, including in
  development.
- Attempts 2 and 3 receive only fixed, allow-listed correction guidance from
  the immediately preceding failure category. Deterministic tests cover
  unsafe text/URL markers, forged IDs, over-eight selections, unparseable
  output, and missing explicit URL-scope items.
- The initial and `invalid_structure` prompts include one compact JSON example
  and the complete global citation contract. The structured schema advertises
  top-level `selected_segment_ids` and per-section `citation_ids` max length 8;
  server validation requires their exact union, candidate binding, five-item
  scope, and explicit-URL coverage. Final Citation projection follows the
  validated top-level selection order.
- The post-refactor real-model `human.he-001` replay is recorded above and
  remains pending for human review; no verdict or live-model quality claim is
  recorded here.

## Paid live-run interpretation

Three pre-refactor real-model `human.he-001` runs were recorded on 2026-08-19.
They remain pending human review and do not receive a pass verdict:

- `20260819T063920Z-6b070818` (`ddfd7b6c35e94053aff368ee762c2e0e`) ended in
  `failed/answer_unavailable`; repeated identical recovery attempts exposed
  that the answer Agent had no bounded feedback from an earlier failure.
- `20260819T065133Z-7dd41999` (`dd7df010369647c7aa40ca47e65456d1`) ended in
  `failed/answer_unavailable`; the answer attempts were classified as
  `invalid_structure`, `invalid_structure`, then `too_many_segments`, showing
  that the structure guidance was still incomplete.
- `20260819T065839Z-8beb1be2` (`1e4d1014b9c64e74a5ba5d2487466ec3`) ended in
  `failed/answer_unavailable` with `too_many_segments` on all three attempts,
  empty Citations, no `runtime_error`, and no deterministic evidence fallback.

The third run validates the intended bounded failure semantics and projection
safety, not answer quality or task success. Its workbook is pending review and
contains no substantive answer, so no human quality conclusion is recorded.

The single post-refactor follow-up run was recorded separately:

- `20260819T072208Z-7b07bfe7` (`b19dde9f64274209b0108a6966cac7ca`) returned
  `status=ok` with no error code and eight citations, all for item `153`.
  The primary diagnostic recorded a timeout; the bounded answer recovery then
  succeeded and the public citation projection completed without a runtime
  error. Its workbook remains `pending_review`; this confirms the recovery and
  projection path only, and does not assign an answer-quality or task-success
  verdict.
