# Technical Design

## 1. Failure chain

The scoped-search authorization fix allows the second search to execute, but live run
`20260818T131242Z-d39e24a6` later reaches the hard 10-tool-call limit. The primary
orchestrator currently returns all accumulated Citations through
`evidence_fallback()`. That run accumulated 32 unique segments, while
`AskNotebookAgentOutput` accepts at most 10, so MCP response projection raises and
the facade rewrites the internally successful answer to public `runtime_error`.

## 2. Recovery boundary

Replace deterministic fallback with a bounded answer-only recovery stage:

```text
primary failure
  -> no trusted evidence: preserve primary failure
  -> trusted evidence: answer Agent attempt 1..3
       -> validated structured answer: status=ok
       -> all attempts exhausted: failed/answer_unavailable, citations=[]
```

The answer Agent is the existing tool-free Composer boundary, extended from a
single validation repair into a common recovery entry point. It receives only the
current question plus trusted current-run evidence. No primary history, tools, raw
tool payloads or mutable runtime state enter this stage.

## 3. Structured selection contract

The answer output first declares one bounded selection, then grounded sections:

```json
{
  "selected_segment_ids": [1309, 1359, 1492, 1616],
  "sections": [
    {"text": "...", "citation_ids": [1309]},
    {"text": "...", "citation_ids": [1359, 1492, 1616]}
  ]
}
```

`selected_segment_ids` carries the schema-level `maxItems: 8`. Server validation
derives or validates these invariants:

- selected items are represented by at least one selected segment;
- every segment belongs to the current candidate allow-list;
- section citation IDs are a subset of the top-level selection and their union equals
  that selection, so no selected evidence is silently unused;
- at most five items and eight unique segments are selected;
- every exact current-message URL reference represented in candidate evidence remains
  selected;
- answer sections cite only selected segments and contain no model-authored source block
  or URL.

The model judges relevance and allocates remaining segment slots. The server owns
scope, bounds and provenance. The same validated Citation list renders visible sources,
persists conversation sources and projects over MCP.

## 4. Attempt accounting

Three means three total answer-model requests. Invalid structured output, invalid
citations, timeout, usage limit, provider failure and unexpected runtime failure each
consume one attempt. Validation feedback is fixed and content-free. The stage has no
output/tool retry hidden outside this explicit loop.

The primary tool-call limit and normal 5/2/3 retrieval budgets remain unchanged.

This follow-up uses focused validation only: schema/validator, three-attempt recovery,
and ChannelService-to-MCP projection. After those pass, run exactly one live
`human.he-001` export for manual review.

## 5. Failure behavior

Delete the product behavior that renders `FALLBACK_INTRO` plus accumulated sources.
After three failed answer attempts, return a typed empty-Citation failure. ChannelService
keeps its existing transient-failure persistence rule, so an undelivered answer does not
become durable conversation history.

## 6. Compatibility and rollout

Terminal actions still win and do not enter answer recovery. Normal valid primary
answers keep their current validation path only when they select at most eight distinct
segments. A larger otherwise-valid primary selection is treated as answer-validation
failure so MCP projection safety applies to every success path. Invalid primary natural
answers and primary failures with evidence converge on the same three-attempt answer stage. Rollback is
limited to the answer pipeline/orchestrator wiring and related schemas/tests; no data
migration or deployment budget change is required.
