# Technical Design

## 1. Observed failure

The Agent can perform a tenant-wide `search_segments` and receive Citations
whose `item_id` values are server-hydrated and tenant-scoped. When the model
then refines its query with one of those item IDs, `_run_search_segments()`
checks only:

```text
AgentActionRuntime.observed_item_ids        current-run inventory/detail reads
TurnContext.recent_inventory.item_id        validated prior inventory context
```

It does not check `AgentDeps.citations`, even though that cache is the trusted
current-run evidence allow-list. The valid refinement is therefore classified
as a forged ID, `invalid_item_scope_attempt` is set, and finalization returns
`item_scope_required`.

## 2. Trust model

Centralize the decision in a small `AgentDeps` method rather than duplicating
set construction inside the tool:

```text
can_scope_search_to_item(item_id)
  = positive integer
  AND item_id in (
        current-run management read observations
        OR validated prior inventory context
        OR current-run trusted Citation cache
      )
```

The Citation branch is safe because `AgentDeps.record()` already rejects
Citations outside `reference_scope`, and Citations originate from
tenant-scoped services. This method grants permission to attempt a scoped read;
`KnowledgeServices.search_segments(..., item_id=...)` still repeats database
tenant, visibility, ready-state and exact-reference predicates.

Do not admit `TurnContext.recent_sources` as item authorization in this task.
Those rows identify prior conversational focus but are intentionally not part
of the current-run Citation allow-list. A follow-up based on prior inventory
remains supported by the existing path.

## 3. Data flow after the fix

```text
global search
  -> tenant/reference-scoped Citations
  -> AgentDeps.record()
  -> trusted current-run item IDs
  -> scoped search(item_id)
  -> KnowledgeServices repeats authorization predicates
  -> evidence/empty result/typed read failure
```

An unknown ID still stops before the service boundary and sets
`invalid_item_scope_attempt`; no backend read occurs.

## 4. Compatibility and safety

- Keep `item_scope_required` for genuinely untrusted IDs.
- Keep exact URL reference scope stricter than all current/prior observations.
- Keep retrieval 5/2/3 budgets and same-step reservation unchanged.
- Keep model-visible tool schemas unchanged.
- Keep diagnostics allow-listed and content-free.
- Update the retrieval-convergence spec if implementation confirms the new
  trusted-Citation item reference rule.

## 5. Validation strategy

Use `FunctionModel` for a deterministic two-search sequence. Track service
calls so the test proves both authorization and backend execution, not merely a
different final error message. Retain the existing forged-ID test as the
negative control. Add an exact-reference negative control and run the broader
tenant/isolation suites before a bounded real-model case replay.

## 6. Rollback

The change should be localized to the scope predicate and tests. Rollback is a
single predicate/method reversion; no schema, migration, persisted state or
deployment configuration changes are required.
