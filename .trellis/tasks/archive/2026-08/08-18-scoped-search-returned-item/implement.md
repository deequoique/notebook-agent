# Implementation Plan

## 1. Reproduce

- [x] Add a deterministic `FunctionModel` regression that performs global
      search, receives item 12, then calls `search_segments(item_id=12)`.
- [x] Assert the pre-fix behavior sets `item_scope_required` and suppresses the
      second backend call; retain the failing test as proof before editing.
- [x] Add safe trace assertions matching the live failure sequence without
      storing prompts, excerpts or tool arguments in reports.

## 2. Fix the authorization predicate

- [x] Add one `AgentDeps` helper for trusted scoped-search item IDs covering
      current management observations, prior inventory context and current-run
      trusted Citations.
- [x] Make `_run_search_segments()` use the helper before any backend call.
- [x] Confirm exact reference filtering occurs before a Citation can authorize
      an item and that service-layer predicates remain unchanged.

## 3. Safety regressions

- [x] Keep the unobserved/forged item regression fail-closed with zero backend
      calls.
- [x] Cover prior inventory context success and prior source/history non-
      authorization.
- [x] Cover exact URL scope with an out-of-scope item and tenant-isolation
      behavior.
- [x] Verify retrieval budgets, read recovery and diagnostics classification
      are unchanged.

## 4. Verification

- [x] Run focused bounded-autonomy and exact-reference suites.
- [x] Run multi-user/tenant, diagnostics privacy, Agent runtime and natural-
      language evaluator regressions.
- [x] Run `py_compile`, Trellis validation and `git diff --check`.
- [x] Run one bounded real-model human case that previously produced
      `item_scope_required`; export its answer for manual review and compare
      only the safe error/tool trace automatically.

## 5. Documentation and handoff

- [x] Update `.trellis/spec/backend/agent-retrieval-convergence.md` with the
      trusted current-run Citation item-reference rule if the fix validates.
- [x] Record before/after reproduction evidence and any remaining model-loop
      failures separately from answer-quality verdicts.
- [x] Do not alter the parent benchmark's human-only verdict policy.

## Review Gates

- Do not replace the fail-closed predicate with tenant membership alone.
- Do not start implementation until the PRD/design/plan is reviewed and the
  task is explicitly moved from `planning` to `in_progress`.
- If allowing Citation item IDs broadens exact URL or cross-tenant access in
  any regression, stop and revise the trust model before proceeding.
