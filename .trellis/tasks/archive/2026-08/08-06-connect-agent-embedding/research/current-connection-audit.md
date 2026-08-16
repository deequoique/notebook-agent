# Current Agent-to-Embedding Connection Audit

Date: 2026-08-06

## Finding

The repository already contains a partial connection; implementation must extend it rather than create a
parallel Agent or retrieval path.

```text
app/bootstrap.py
  ZhipuEmbedder (only when ZHIPU_API_KEY exists)
    → KnowledgeServices(embedder=...)
      → bm25_search(... tenant ...)
      → embed([query])
      → vector_search(... tenant ...)
        → hydrate Citation
          → PydanticAI tool result
```

CLI `ask` separately constructs the same embedder and services. This is the main composition drift risk.

## Gaps to close

1. Missing gateway embedding credentials silently disable vector search while allowing ordinary Agent
   answers from lexical results.
2. `KnowledgeServices` types the provider as `Any | None`; ingestion and query retrieval have no shared
   protocol contract.
3. `ZhipuEmbedder` validates response count but not each vector's configured dimension or finite values.
4. No test crosses PydanticAI tool invocation, query embedding, real pgvector SQL, tenant filtering,
   citation hydration, and final evidence enforcement in one path.
5. CLI and channel gateway duplicate composition rules.

## Latest real-channel observation

A user-provided WeChat screenshot shows an apparently single non-command inbound message receiving two
failure responses: first the Agent evidence guard reported that required retrieval had not happened, then
the channel reported temporary unavailability. The screenshot, original message, timestamp and external
identity are intentionally not copied into the repository.

What this proves:

- WeChat adapter → bridge plugin → gateway → PydanticAI is reachable.
- The probe did not execute `search_segments`, so it does not prove query embedding or pgvector retrieval.
- The one-inbound/one-reply invariant is not currently demonstrated.

What remains unknown until a sanitized reproduction:

- whether the platform delivered the same event twice;
- whether the plugin treated a valid fail-closed `AgentAnswer` as an exception after replying;
- whether LangBot's required-plugin guard appended its availability response after the plugin reply.

Implementation must trace only internal correlation/message identifiers and status/error classes. It must
not persist or print the private message or external sender identity.

## Result-state clarification

The product requirement distinguishes two outcomes that must never share copy or status:

- successful retrieval with zero tenant-owned evidence is `not_found`;
- embedding, database, timeout, configuration, or tool failure is `failed`.

The channel bridge must preserve `AgentAnswer.status`, `error_code`, and text instead of flattening every
non-success result into channel unavailability. `search_required` and a final `answer_unavailable` after
repair exhaustion are failures, not evidence that the user's knowledge base has no matching data. A raw
citation mismatch is internal-only and must never become a channel answer.

## Invalid citation recovery clarification

An unknown citation marker in a model draft is a model citation hallucination or formatting error. The
existing runtime catches it after the run and returns a technical `citation_required` failure, so fabricated
evidence is blocked, but the internal guard becomes user-visible and no repair is attempted.

The required behavior is now:

1. discard the invalid draft before it reaches `AgentAnswer`, persistence, or a channel;
2. use a bounded PydanticAI output-validator retry;
3. require at least one new `search_segments` call after the mismatch, then validate again against the
   accumulated tool-returned citation allow-list;
4. return only the repaired, evidence-backed answer;
5. if repair is exhausted, return one generic `failed/answer_unavailable` message without the invalid ID,
   rejected draft, guard name, private question, or evidence content.

The local PydanticAI runtime exposes output validators and `ModelRetry`, with output-retry budget accounting,
so this recovery can remain inside the existing Agent run and usage limits rather than creating an unbounded
second Agent workflow.

## Existing invariants to preserve

- `vector_search()` and `bm25_search()` filter by `ContentItem.user_id`.
- `KnowledgeServices` rechecks owner when hydrating segments/items.
- Model tool schemas do not expose `user_id`.
- Deterministic identity/session commands do not call the Agent.
- Agent answers require a real search call and valid returned citation IDs.
- RRF/reranking and re-embedding are outside the parent task's first-release scope.

## Implementation conclusion

The smallest correct change is to formalize the shared embedding provider boundary, make query vector
generation mandatory for ordinary knowledge search, unify composition, validate vectors, and add a real
PostgreSQL integration test. No new Agent framework, channel adapter, database schema, or ranking system
is required.
