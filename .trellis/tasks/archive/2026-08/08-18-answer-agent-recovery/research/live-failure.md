# Live failure evidence

- Run: `20260818T131242Z-d39e24a6`
- Request: `41e8fd028e4b485b9f608a82bde78025`
- Two `search_segments` calls succeeded; `item_scope_required` did not recur.
- The primary Agent attempted an eleventh tool call against a hard limit of ten.
- The run had 32 unique trusted Citation segments and entered the current deterministic
  evidence fallback.
- ChannelService emitted `gateway_response_ready` without an error code, then MCP
  projection rejected the Citation list because its public maximum is ten.
- `McpToolFacade` caught that projection exception and returned public
  `failed/runtime_error` with “知识库服务暂时不可用”。

This task replaces the fallback behavior rather than raising any limit.

## Implementation evidence

The deterministic regression set now covers:

- a primary tool-call failure with trusted evidence recovering through the
  first bounded answer-agent attempt;
- two invalid answer drafts followed by a valid third draft, with exactly
  three provider calls;
- three invalid drafts returning `failed/answer_unavailable` and empty
  Citations;
- selection of a segment after the ninth retrieval-order position;
- all evidence-bearing videos in an exact URL scope being required, including
  the unsatisfiable more-than-five-video scope, which exhausts safely instead
  of silently dropping a URL item;
- provider, timeout, output-limit, exact-read-retry, terminal-action, and
  invalid scoped-search regressions.

The answer Agent receives the complete scope-filtered current-run Citation
set. Eight is enforced only by server-side output validation; it is not used
as a retrieval-order prefilter. Recovery allows exactly three total attempts;
each uses `request_limit=1`, `output_retries=0`, and the existing answer
timeout/output limits. No deterministic evidence fallback path or fallback
text remains in the primary or answer pipeline. Primary natural answers with
more than eight distinct markers now enter the same answer recovery path. A
recovered MCP success with nine available segments projects exactly eight
citations. Exhausted recovery has no `new_messages`, does not regain
composable read action results, and ChannelService does not persist the failed
transient answer.

The live failure also exposed a recovery-quality issue: each Composer run was
independent, so an invalid draft or an unparseable response gave attempts 2
and 3 no correction signal. The recovery state now retains only an
allow-listed failure category and renders a fixed correction sentence in the
next attempt's instructions. Categories cover malformed structure, unsafe
section text, missing/invalid citations, item/segment bounds, missing explicit
scope items, and provider failure. Rejected draft text, questions, IDs,
URLs, excerpts, and provider payloads are never retained or echoed. The
initial and invalid-structure guidance include one compact valid JSON example
and repeat the complete global contract, while `AnswerSection.citation_ids`
advertises a per-section maximum of eight; server validation still enforces
the global distinct-segment limit across all sections.

Focused checks completed:

```text
answer/retrieval/action/MCP/diagnostics/persistence/recovery-feedback/schema focused suites  160 passed
tests/test_multiuser_integration.py            1 passed, 17 skipped
py_compile and git diff --check                passed
```

The complete local suite currently reports `646 passed, 73 skipped, 6
failed`; the six failures are unrelated browser-origin, loopback-socket, and
environment/configuration checks already present in the shared worktree.

The real-model `human.he-001` replay remains pending; no paid live benchmark
was run during the initial implementation. Three paid reruns were later
recorded on 2026-08-19:

1. `20260819T063920Z-6b070818` / request
   `ddfd7b6c35e94053aff368ee762c2e0e` returned `failed/answer_unavailable`.
   The attempts repeated the same failure, exposing that independent answer
   runs had no bounded feedback from the previous attempt.
2. `20260819T065133Z-7dd41999` / request
   `dd7df010369647c7aa40ca47e65456d1` returned `failed/answer_unavailable`.
   Safe attempt reasons were `invalid_structure`, `invalid_structure`, and
   `too_many_segments`; the run exposed incomplete structure guidance.
3. `20260819T065839Z-8beb1be2` / request
   `1e4d1014b9c64e74a5ba5d2487466ec3` returned
   `failed/answer_unavailable` after `too_many_segments` on all three
   attempts. It returned empty Citations, did not become `runtime_error`, and
   did not use deterministic fallback.

The third run validates failure semantics and MCP projection safety, not
answer quality or task success. All three workbooks remain
`pending_review`; the third contains no substantive answer, and no human pass
verdict is assigned.

## Post-refactor human review run

The one follow-up paid run after the top-level selection refactor was recorded
separately from the three earlier pre-refactor failures:

- Run: `20260819T072208Z-7b07bfe7`
- Request: `b19dde9f64274209b0108a6966cac7ca`
- Public result: `status=ok`, `error_code` absent
- Citations: 8, all for item `153`
- Primary diagnostic: timeout
- Recovery: answer Agent succeeded and public projection completed
- Human review: `pending_review`

This run is evidence that the bounded answer recovery can complete the public
projection after the primary timeout. It is not an automated answer-quality or
task-success verdict; the human reviewer owns that decision.
