# Reproduction and root-cause evidence

## Live evidence

Run `20260818T122606Z-b5bc3410` recorded all 20 human answers without assigning
automatic verdicts. Seven cases returned `item_scope_required`:

```text
human.he-001
human.he-002
human.he-006
human.he-007
human.he-009
human.he-011
human.he-020
```

Their safe tool traces commonly begin with `search_segments`, followed by
`get_item`, `get_neighbors`, or a refined `search_segments`. The error is a
runtime scope decision, not a human answer-quality verdict.

## Code evidence

- `app/agent/agent_tools/retrieval.py::_run_search_segments` accepts an
  `item_id` only when it is present in
  `actions.is_observed_item(item_id)` or `context.inventory_item_ids`.
- The same function records successful search results in
  `AgentDeps.citations` through `deps.record(citations)`.
- `AgentDeps.record` enforces exact `reference_scope` before inserting a
  Citation into that cache.
- The authorization predicate never consults those current-run trusted
  Citations, so a model cannot refine within an item returned by its own first
  knowledge search.
- `KnowledgeAgent` maps any run ending with `invalid_item_scope_attempt` to the
  public `failed/item_scope_required` response, including usage-limit and
  unexpected-model-behavior exits.

## Existing controls

`tests/test_bounded_autonomy_runtime.py` already proves:

- a current-run inventory result may authorize scoped search;
- validated prior inventory context may authorize scoped search;
- an unobserved item ID fails closed and performs zero backend searches.

The missing case is a current-run search Citation authorizing a second scoped
search. This is the deterministic reproduction to add before implementation.

## Deterministic before/after reproduction

The new `FunctionModel` case in
`tests/test_bounded_autonomy_runtime.py::test_current_run_search_citation_can_scope_follow_up_search`
first returns a Citation for item `12`, then requests a second
`search_segments(item_id=12)` call. Running that test before the predicate
change failed at the expected `status == "ok"` assertion (`actual: failed`).
The old `_run_search_segments` branch only consulted management observations
and prior inventory IDs, so the second service call was rejected before the
backend boundary and the run was finalized as `item_scope_required`.

After the change, the same test passes with the service tracker recording
`item_ids == [None, 12]` and two `search_segments` calls. The scoped result is
the only selected final Citation. The centralized `AgentDeps` predicate now
combines positive current-run management observations, validated prior
inventory context, and current-run Citations already accepted by
`AgentDeps.record` (which applies exact reference filtering first).

The safety controls remain covered by deterministic negatives:

- item `999` still returns `failed/item_scope_required` with zero backend
  searches;
- a prior source/history reference for item `12` alone is rejected with zero
  backend searches;
- under an exact current-message URL scope, the first search deliberately
  supplies both an out-of-scope item `1` Citation and the in-scope item `2`
  Citation to `AgentDeps.record()`. The record-time reference filter drops
  item `1`; the subsequent `search_segments(item_id=1)` attempt is rejected
  and makes zero second backend calls;
- a validated prior inventory reference for item `1` also cannot authorize
  that item under an exact item `2` reference scope, and its attempted scoped
  search makes zero backend calls.

The last control was added during independent review. The first implementation
still allowed management observations and prior inventory context to satisfy
the item predicate while an exact reference scope was active. Although
`KnowledgeServices` repeated the tenant, state, item, and reference predicates
and therefore prevented an unauthorized result, the attempt violated the
stricter runtime boundary. `can_scope_search_to_item()` now accepts only a
current-run Citation already filtered by `record()` whenever
`reference_scope` is non-empty; management observations and prior inventory
remain valid sources only for non-exact-scope turns.

## Validation evidence

Focused and proportionate regression runs completed after the fix:

```text
tests/test_bounded_autonomy_runtime.py
tests/test_exact_video_reference_routing.py  30 passed (combined focused run)
tests/test_agent_runtime.py                  19 passed
tests/test_diagnostics.py                    30 passed
tests/test_natural_language_evaluator.py     24 passed
tests/test_knowledge_services.py
tests/test_agent_context.py
tests/test_bounded_recovery_runtime.py        23 passed (combined service/context/recovery run)
tests/test_multiuser_integration.py            1 passed, 17 skipped
```

Across these non-overlapping runs, `127 passed` and `17 skipped`. The focused
bounded-autonomy plus exact-reference run accounts for 30 of the passes.

Final main-session verification across the broader selected suites reached
`138 passed` and `17 skipped`. `py_compile`, `git diff --check`, and Trellis
context validation also passed. The multi-user suite's skipped cases require
unavailable integration fixtures, and human-review verdict semantics were not
changed.

## Bounded live replay

Run `20260818T131242Z-d39e24a6` replayed `human.he-001`, which had previously
returned `item_scope_required`. The new safe trace contains two succeeded
`search_segments` calls and no `item_scope_required`; the scoped follow-up
therefore crossed the intended backend boundary. The turn later ended with a
separate `limit` diagnostic normalized to public `runtime_error`. Its answer is
exported as `pending_review`; this task claims only that the invalid scope
rejection is fixed, not that the independent convergence/limit issue is solved.
