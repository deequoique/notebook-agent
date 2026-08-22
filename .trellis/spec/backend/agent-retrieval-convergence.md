# Agent Retrieval Convergence and Multi-Source Answers

## 1. Scope / Trigger

Use this contract when changing PydanticAI knowledge tools, retrieval budgets,
candidate selection, citations, source rendering, Agent usage limits, or the
knowledge-answer persistence path. It applies only to tenant-scoped knowledge
answers. Video save and confirmation actions retain their existing terminal
`ActionOutcome` behavior and do not consume retrieval budget.

Bounded autonomy is the only runtime. One primary Turn Agent chooses whether
the current message needs private-knowledge tools and returns natural text.
The application chooses the finalization path from trusted tool/action traces
rather than a model-authored answer mode:

```text
trusted request + bounded context -> Turn Agent <-> visible atomic tools
                                  -> terminal action wins, or
                                  -> canonical read-only result, or
                                  -> structured grounded Composer + server sources
```

Knowledge answers use a bounded primary Turn Agent followed by a structured
Answer Composer whenever a search returns candidates. Tenant identity,
deleted/non-ready gates, pending confirmation, idempotency, side-effect claims,
hard tool/request/output/time limits, and the current-run Citation allow-list
remain server-owned.

### 1.1 Bounded-autonomy contracts

- Social replies, capability replies, and missing-context clarification may
  finish with zero knowledge tools. Such text must not contain Citation
  markers, URLs, or a model-authored source block. A current-message supported
  URL plus a content question still requires a tenant-scoped search.
- A successful knowledge search with candidates enters the structured Composer.
  Grounded sections cite positive `[S<segment id>]` values from the current-run
  Citation cache; unsupported sections carry no model text or citations and
  render a fixed server-owned evidence-insufficiency notice. The server
  appends source titles, URLs, timestamps, and excerpts.
- A provider-streamed knowledge answer is citation-first: after the current-run
  plan and Citation allow-list pass, each grounded section follows
  `section_started -> text_delta* -> section_completed` (with
  `section_aborted` for technical interruption). Citation authorization proves
  only tenant/current-run source ownership and traceability; it is not a
  semantic fact verifier.
- If a provider is empty or does not support streaming before any section is
  public, use the existing whole-answer one-delta compatibility path. Once a
  section is public, provider failure aborts the turn rather than silently
  starting a second answer execution.
- Invalid grounded text or a failed primary run with trusted Citations enters a
  tool-free answer Agent against the same server-filtered evidence allow-list.
  It receives exactly three total provider attempts; invalid output, timeout,
  usage limit, provider failure, and runtime failure each consume one attempt.
  A successful structured selection returns validated server-rendered sources;
  three failures return `failed/answer_unavailable` with empty Citations. Each
  attempt has its own `RunUsage`; no primary-run budget is increased or
  mechanically subtracted.
- Inventory/detail reads are non-terminal observations, so
  a turn may list items and then search within an item returned by the current
  run or bounded trusted prior inventory context. A successful current-run
  knowledge search is also a trusted Citation observation: after exact
  current-message reference filtering, its Citation item may authorize a
  subsequent scoped search in the same run. Prior source focus and raw model
  history alone never authorize an item. Domain services repeat tenant,
  active/deleted, ready-state, and item predicates. If no
  knowledge search follows, visible text and history use canonical
  server-rendered read text rather than unconstrained model prose.
- Save, management mutation, confirmation, cancellation, restore, and explicit
  ingestion retry remain terminal canonical outcomes. They are never retried
  by Agent recovery, and model prose cannot replace their result.
- Save and management tools are always registered. Tool prepare policy hides
  pending decision tools without a matching cached trusted pending snapshot
  and hides unrelated management/pending tools for semantic content questions. Bare URL
  save confirmation remains a deterministic pre-model route.
- `todo_write` is optional working memory for one dependent multi-step turn.
  It stores at most six short items with only `pending`, `in_progress`,
  `completed`, or `blocked` states and at most one `in_progress` item. It is
  discarded after `run()`, never authorizes a tool or mutation, and is never
  persisted, added to history, or logged with its content.
- Expected read failures expose only an allow-listed `ErrorEnvelope` and a
  server-issued recovery grant. An exact read fingerprint may be retried once;
  all read recovery actions together are capped at two. Empty search is an
  observation and a true changed-query reformulation consumes one recovery
  action. Mutation, confirmation, provider/model, policy/security,
  tenant/scope, deleted/non-ready, and side-effect-indeterminate failures
  receive no autonomous retry; the separate answer Agent owns its fixed three
  attempts.
- `ContextBuilder` projects only completed current-tenant/thread inventory and
  prior source focus after current item/segment availability checks. Historical
  segment IDs help resolve conversation focus but never enter the current-run
  Citation allow-list. Failed ownership validation omits source focus rather
  than broadening trust.

## 2. Signatures

```python
NORMAL_RETRIEVAL_CALLS_LIMIT = 5
NORMAL_SEARCH_CALLS_LIMIT = 2
NORMAL_EXPANSION_CALLS_LIMIT = 3
MAX_SOURCE_ITEMS = 5
SEARCH_RESULT_LIMIT = 10
SEARCH_CANDIDATE_POOL_LIMIT = 50
COMPRESSED_EVIDENCE_LIMIT = 8
COMPOSER_EVIDENCE_EXCERPT_CHARS = 360

class AgentDeps:
    citations: dict[int, Citation]       # keyed by segment_id, insertion order
    last_retrieval_run_step: int | None
    def reserve_retrieval(
        self, *, run_step: int, kind: RetrievalKind
    ) -> ReservationResult: ...

class RetrievalToolPayload(TypedDict):
    status: Literal["ok", "skipped"]
    evidence: list[dict]
    reason: Literal["same_model_step", "budget_exhausted"] | None

class AnswerSection(BaseModel):
    text: str
    citation_ids: list[int]  # 1..8 per section; global distinct union <= 8

class AnswerDraft(BaseModel):
    selected_segment_ids: list[int]  # 1..8; final server-owned selection
    sections: list[AnswerSection]

class ComposerDeps:
    citations: dict[int, Citation]       # prompt rows and validator allow-list
    excerpt_chars: int
    required_item_ids: frozenset[int]
    max_segments: int
```

`AGENT_REQUEST_LIMIT`, `AGENT_TOOL_CALLS_LIMIT`, and
`AGENT_OUTPUT_TOKEN_LIMIT` remain deployment safety limits. The primary Turn
Agent and answer Agent each receive a new `RunUsage`; the configured
output-token limit is therefore per stage, not a cumulative allowance. The
answer Agent may make at most three total attempts, with a fresh usage object
for each attempt.

`AGENT_COMPOSER_MAX_TOKENS` defaults to 1000 and is the real provider-side cap
for each answer-agent attempt. It must be positive and must not exceed
`AGENT_OUTPUT_TOKEN_LIMIT`. Each attempt uses `request_limit=1`,
`output_retries=0`, and one answer-stage wall-clock timeout.

## 3. Contracts

- Every provider request includes `parallel_tool_calls=False`, but this is an
  advisory provider hint, never a correctness boundary.
- The retrieval Agent uses local sequential tool execution. Before a retrieval
  tool reaches a service, `AgentDeps.reserve_retrieval()` holds one lock and
  atomically checks the current `run_step`, total 5-call budget, search 2-call
  budget, and expansion 3-call budget. Only its first successful reservation
  in a model step invokes a backend service.
- Other retrieval calls in the same provider batch return a typed
  `skipped/same_model_step` payload. Calls after a exhausted stage budget
  return `skipped/budget_exhausted`. Neither kind performs embedding, SQL, or
  storage work, records a Citation, or pretends that a search found no hits.
- `search_segments` is public-limit bounded to 10. It obtains a bounded
  over-fetch pool (`min(50, max(20, limit * 5))`) from each hybrid retrieval
  backend, removes exact duplicate segment IDs using the best score, ranks
  item groups by their best hit, chooses at most five items, emits one best
  representative for each, then fills remaining slots by score with distinct
  segments from those selected items. All database hydration remains tenant
  scoped.
- A social/capability answer may finish without retrieval. Once a knowledge search succeeds, empty results remain distinct from transient read failure. Clean final empty search state produces server-owned `not_found/no_evidence`; non-empty evidence always enters the structured Composer. Exhausted read recovery returns `read_unavailable`, while a composable inventory read may still return bounded canonical partial read text.
- Primary-agent usage-limit or timeout failures with trusted citations enter
  the bounded answer Agent. Without citations they use phase-accurate
  failed-limit or timeout behavior. The raw hard limits remain defense in
  depth; increasing them is not a convergence fix.
- Browser/API transport deadlines must cover all independently bounded model
  stages plus a small dispatch grace period. They must not reuse one stage's
  `AGENT_TIMEOUT_SECONDS` as the whole-request deadline, because doing so can
  cancel the evidence-backed answer/failure path as retrieval expires.
- The answer Agent has no retrieval or action tools. It receives only the user
  question and all bounded current-run Citation title, excerpt, timestamp, and
  segment-ID candidates. It uses `PromptedOutput(AnswerDraft)` to parse
  schema-prompted JSON text without an output tool or `tool_choice=required`.
  The server validates that every selected and cited ID is allowed, grounded sections have evidence, unsupported sections contain no model-authored text or citations, selected text contains no model-authored URL/source block, the selection has at most five distinct items and eight distinct segments, and every server-owned item constraint remains satisfied. The answer Agent
  itself chooses relevance and segment allocation; eight is an output cap, not
  a retrieval-order prefilter. It has no output retry and never starts a fresh
  search. When an attempt is rejected, the next attempt may receive only one
  fixed, allow-listed failure category and concise correction guidance
  (`invalid_structure`, `unsafe_text`, `missing_citation`,
  `invalid_citation`, `too_many_segments`, `too_many_items`,
  `missing_scope_item`, or `provider_failure`). Previous drafts, questions,
  Citation values, URLs, and provider payloads never enter the feedback.
- Every Composer request sends `AGENT_COMPOSER_MAX_TOKENS` as the provider's
  actual `max_tokens` generation cap. For DeepSeek Chat Completions, the model
  profile must retain DeepSeek response/tool semantics, map the field to
  `max_tokens` rather than `max_completion_tokens`, and send
  `thinking: {"type": "disabled"}` without `reasoning_effort=none`.
  Other compatible Composer models request provider-neutral `thinking=False`
  when supported. Retrieval model settings remain unchanged.
- The server projects all bounded current-run evidence in retrieval order for
  answer selection. Invalid citations, output-token exhaustion, provider
  failures, and timeouts consume one of the three answer attempts. No
  deterministic evidence fallback is returned.
- The application appends `[S<segment_id>]` markers after validating the
  structured draft, and source rendering owns titles and real URLs. Sources
  are grouped once per item in retrieval order, retain distinct timestamp
  evidence under the item, and never infer chapter titles.
- Knowledge answers may use restrained Markdown only when it improves
  readability: paragraphs, short headings, ordered/unordered lists, emphasis,
  blockquotes, and inline code. The shared text contract applies to every
  channel, including channels that display Markdown punctuation literally.
  Model text must not contain Markdown links, images, raw HTML, or a
  source/reference section. Composer text never writes `[S…]`; it returns
  structured `citation_ids` and the server appends exact markers. A bounded
  natural answer must leave each exact `[S<positive segment id>]` marker as
  ordinary text, never link it, wrap it in code, or replace its spelling.
- Answer validation failure, timeout, provider failure, or usage-limit failure
  discards the draft and consumes an attempt. After three failures, the public
  result is `failed/answer_unavailable` with empty Citations and no answer
  history. Successful knowledge answers persist only the normalized user
  question plus final visible answer, while the conversation turn keeps the
  same validated public Citation selection. Tool payloads, intermediate Turn
  Agent text, answer prompts, and invalid drafts are never persisted.
- A streamed section is temporary until the final successful response is
  assembled. Client disconnect, cancellation, timeout, provider abort, or an
  incomplete section never persists a partial `ConversationTurn`.
- Diagnostics use only fixed safe fields. Retrieval events carry
  `agent_phase=retrieval`; composer events carry `agent_phase=answer`.
  `tool_outcome=skipped` is allowed. Answer-stage retries project only a safe
  error class, attempt index, failure category, allow-listed validation
  `failure_reason`, and validated integer `http_status` when applicable; they
  never pass provider exception objects.
  Primary retrieval `ModelHTTPError` diagnostics retain the existing
  development-only detail policy, while production forbids its body and
  message. Production logs never include questions, tool arguments/results,
  excerpts, IDs, drafts, URLs, or exception messages.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| provider emits several retrieval calls in one response | exactly one backend retrieval runs; remaining calls are typed skipped results |
| normal search/expansion budgets are exhausted | no further backend retrieval; existing trusted evidence remains usable |
| successful searches have no evidence | bounded empty-search recovery or `not_found/no_evidence`, no answer-agent recovery |
| primary timeout or usage limit after evidence | log retrieval phase/kind and run the three-attempt answer Agent |
| Web request reaches one retrieval-stage timeout with evidence | transport remains open for answer attempts; do not return a premature 504 |
| primary timeout or usage limit without evidence | fail closed with phase-accurate wording |
| non-empty search candidates | enter structured Composer; unsupported sections render fixed server text |
| natural answer has unknown/missing IDs or six item IDs | run the bounded Composer against the same evidence allow-list |
| answer draft is invalid, truncated, over limit, timed out, or provider fails | consume one of three answer attempts; after exhaustion return `failed/answer_unavailable` with no Citations or draft persistence |
| provider stream is empty/unsupported before any section is public | use one whole-answer compatibility delta; do not expose a section lifecycle |
| provider stream fails after a section is public | emit/propagate section abort and a failed terminal state; do not silently replay the answer |
| streamed section is cancelled, disconnected, timed out, or incomplete | remove the temporary section and persist no partial conversation turn |
| provider HTTP request fails | preserve phase behavior; answer-stage retries log only safe status/class; primary retrieval follows the development-only detail policy |
| valid answer draft | server-rendered markers and grouped real sources, at most five items |
| action succeeds, including a mixed tool batch | canonical action result wins and composer does not run |
| read failure exhausts its bounded recovery | `failed/read_unavailable` without evidence, otherwise the bounded canonical partial read result; trusted Citations enter the three-attempt answer Agent |

## 5. Good / Base / Bad Cases

- Good: a provider ignores its parallel-tool-call hint and emits two searches;
  the first executes, the second gets `same_model_step`, and the structured
  Composer uses only the cached source IDs.
- Good: one video dominates raw segment scores but bounded over-fetch exposes
  five relevant item groups. The answer shows one top-level row per video and
  preserves two distant links for the selected first video.
- Base: one search provides sufficient evidence. The Composer returns a
  valid cited answer, and canonical history contains only the normalized
  question and final answer.
- Bad: rely on `parallel_tool_calls=False` alone, treat skipped tools as zero
  results, rerun search to fix citation formatting, bypass Composer after
  non-empty retrieval, fabricate chapter names, or log private evidence or
  exception text.

## 6. Tests Required

- A batched `FunctionModel` returns two searches, then two neighbor calls and
  one metadata call with non-zero `RequestUsage.output_tokens` totaling 2066.
  Assert one backend retrieval per model step, typed skipped payloads, no
  extra embedding/SQL work, and a trusted final answer or bounded answer-agent
  recovery result.
- Cover normal 5/2/3 convergence, zero-hit exit, hard request/tool limits,
  phase-correct output-token diagnostics, and retrieval embedding/database
  failures.
- Cover direct valid natural answers, three total same-evidence answer-agent
  attempts, invalid-draft exhaustion, timeout exhaustion, provider-error
  exhaustion, provider-cap request serialization, and output-token exhaustion.
  Assert recovery starts no retrieval, feeds only fixed validation categories
  to attempts 2/3, returns `answer_unavailable` with empty Citations after the
  third failure, and persists no invalid model content.
- Cover Markdown inside a valid answer-agent section and a bounded natural
  answer; assert structured citation selection and exact-marker validation are
  unchanged, while model-authored URLs/source headings remain rejected.
- Cover hybrid duplicate collapse, one-item crowding, six-item selection,
  distant same-item segments, public limit clamping, bounded candidate pool,
  and PostgreSQL tenant predicates during hydration.
- Re-run action/pending-confirmation, persistence, duplicate message,
  multi-user tenant isolation, source grouping, diagnostics privacy, and the
  complete test suite.

## 7. Wrong vs Correct

#### Wrong

```python
# A provider can ignore this preference and execute an entire batch.
result = await turn_agent.run(..., model_settings={"parallel_tool_calls": False})

# Formatting failure wastes an embedding request and loses the original budget.
if invalid_citation:
    return await turn_agent.run(question_again)
```

#### Correct

```python
with turn_agent.parallel_tool_call_execution_mode("sequential"):
    await turn_agent.run(...)

# The locked reservation decides whether a retrieval backend may run.
if deps.reserve_retrieval(run_step=ctx.run_step, kind=kind) is not EXECUTE:
    return {"status": "skipped", "evidence": [], "reason": "same_model_step"}

# Each tool-free answer-agent attempt can use only trusted cached evidence;
# the outer recovery stage allows at most three total attempts.
answer = await composer.run(question, deps=ComposerDeps(allowed_citations))
```

## Scenario: Video URL context and optional item narrowing

### 1. Scope / Trigger

Use this contract whenever the current user message contains one or more
supported video URLs. A URL is trusted model context, not an automatic exact
retrieval boundary. The model may search the whole current tenant or provide an
optional `item_id`; the service remains the hard authorization boundary.

### 2. Signatures

```python
@dataclass(frozen=True)
class ParsedMessageReferences:
    ordered_urls: tuple[str, ...]       # preserves order and duplicates
    supported_urls: tuple[str, ...]
    unsupported_urls: tuple[str, ...]
    references: tuple[tuple[str, str], ...]  # unique (platform, platform_id)
    semantic_remainder: str
    @property
    def is_bare_supported_url_batch(self) -> bool: ...

def parse_message_references(message: str) -> ParsedMessageReferences: ...

class KnowledgeServices:
    def set_reference_scope(
        self, references: Iterable[tuple[str, str]] | None
    ) -> None: ...

def vector_search(
    db, query_vector, *, user_id: int, k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
) -> list[Hit]: ...

def bm25_search(
    db, query: str, *, user_id: int, k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
) -> list[Hit]: ...
```

The current tenant is the only unrestricted retrieval boundary. Optional
`item_id` filtering is rechecked by the service for tenant ownership, active,
non-archived, and ready state. Model arguments never supply tenant or user IDs.

### 3. Contracts

- URL extraction and normalization are server-owned and use the same
  `normalize_item_reference()` contract as ingestion. Strip only allow-listed
  trailing punctuation. Adjacent CJK text terminates the URL token and remains
  semantic text.
- A bare batch of 1–10 supported URLs routes directly to the existing durable
  save-confirmation action before retrieval-service construction or any model
  request. Preserve original URL order and duplicates. Existing unavailable,
  invalid, unsupported, and batch-limit action outcomes remain authoritative.
- A supported URL plus semantic text is passed as model context; it does not create an automatic exact retrieval scope. The model may use tenant-wide search or an optional `item_id`, while the service enforces tenant and item readiness/visibility. Conversation history cannot broaden server authorization.
- Vector search, lexical search, result hydration, neighbor hydration, item
  metadata, and timestamp resolution repeat tenant, active/deleted, ready-state,
  and exact-reference predicates as applicable. Defense-in-depth citation
  filtering runs before evidence reaches the Turn Agent's trusted Citation cache.
- An empty search is a successful empty tool result, not a retrieval failure. Clean final emptiness produces server-owned `no_evidence`; candidates invoke Composer.
- Explicit URL content questions hide inventory, mutation, and pending-save
  tools. `save_videos` is exposed only when the semantic text contains a
  conservative, positive current-message save command; a negated save phrase or
  a content question cannot inherit save intent from history.
- Ordinary messages without supported URLs retain unrestricted tenant-scoped
  hybrid retrieval, management-history follow-ups, convergence budgets, and
  Composer behavior.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| bare supported URL batch | durable `save_confirmation_required`; zero model requests; no retrieval service |
| bare unsupported or malformed URL | existing safe validation code; no accidental supported confirmation |
| URL plus semantic content question | model may use tenant-wide search or optional item narrowing; citations remain tenant-scoped |
| optional item absent, deleted, non-ready, or without evidence | empty successful lookup; no cross-tenant or inactive-item fallback |
| model reuses an out-of-scope segment/item ID from history | ID never enters trusted citations |
| URL content question while an old save/delete action is pending | current question remains retrieval-only; old pending action is unchanged |
| explicit positive “save this URL” command | `save_videos` may be exposed; current-message URL equality checks still apply |
| ordinary free-text question | existing hybrid retrieval and management tool behavior unchanged |

### 5. Good / Base / Bad Cases

- Good: history discusses video A, but the current question names video B. The
  model can search tenant-wide or narrow with B's `item_id`; service checks still
  prevent cross-tenant or inactive-item access.
- Good: the current message is a bare URL duplicated three times. The durable
  confirmation receives the three original values in order without a model.
- Base: no supported URL is present. The existing tenant-wide Top-5 retrieval
  and management-history behavior runs unchanged.
- Bad: accept an `item_id` from another tenant or an inactive item, or rely only
  on prompt wording to keep stale history from choosing a write tool.

### 6. Tests Required

- Parse short/canonical YouTube URLs, trailing punctuation, adjacent Chinese
  question text, duplicates, unsupported hosts, and semantic remainders.
- Assert a bare supported URL and batch perform zero model calls, preserve
  order/duplicates, and do not construct retrieval services.
- Reproduce saved video A versus current URL B and assert A never appears in
  tool payloads, trusted citations, Composer input, or the visible answer.
- Cover ready, absent, deleted, pending, failed, and no-evidence referenced
  items; no unavailable state may fall back to another active video.
- Attempt out-of-scope `get_neighbors`, `get_item`, and `open_at` calls and
  assert empty results plus one truthful tool outcome sequence.
- Assert scoped URL content questions hide management and pending/save tools,
  while a positive current-message save command exposes only the valid save
  route.
- Re-run ordinary Agent retrieval, action confirmation, management pagination,
  duplicate delivery, deleted-content, and PostgreSQL tenant-isolation tests.

### 7. Wrong vs Correct

#### Wrong

```python
# The nearest tenant hit may be a completely different video.
citations = services.search_segments(video_id)

# Prompt text is not an authorization or subject boundary.
instructions += "Please use the URL from the current message."
```

#### Correct

```python
parsed = parse_message_references(current_question)

if parsed.is_bare_supported_url_batch:
    return actions.request_confirmation(list(parsed.ordered_urls))

services.set_reference_scope(parsed.references or None)
# SQL predicates and citation validation both enforce the exact scope.
citations = services.search_segments(current_question)
```
