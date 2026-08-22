# Modularize and unify Agent runtime — technical design

## 1. Current problem

`app/agent/runtime.py` owns seven different kinds of behavior:

1. prompts, constants, and scope validation;
2. mutable run state and output contracts;
3. dynamic tool visibility and recovery policy;
4. retrieval, save, confirmation, and management tool definitions;
5. primary PydanticAI Agent construction;
6. Composer construction and evidence rendering;
7. the product-level run lifecycle, exception mapping, and finalization.

This creates a high-risk editing surface. A reader cannot understand one tool
without traversing orchestration and answer-generation code, while a lifecycle
change can accidentally alter tool visibility, persistence, or diagnostic
privacy. Tests also import several implementation symbols from this module, so
a simple file move would break compatibility.

The module also carries rollout complexity that is no longer wanted: a legacy
planner/Composer path, an opt-in bounded-autonomy path, optional save tools, and
optional item-management tools. The target architecture has one runtime and
one capability set.

## 2. Design principles

- Separate structural extraction from runtime unification. Preserve behavior
  mechanically while moving code, then delete the obsolete branches behind
  focused tests.
- Keep trust boundaries explicit. Model-visible tool payloads, server-owned
  dependencies, citations, actions, and public answers remain separate types.
- Point dependencies inward from orchestration to focused helpers. Tool modules
  never import `KnowledgeAgent` or answer finalization.
- Preserve one compatibility seam. Existing callers continue to import from
  `app.agent.runtime`; new code may import focused implementation modules.
- Prefer named collaborators over passing many unrelated callbacks through
  nested functions.

## 3. Proposed module layout

```text
app/agent/
├── runtime.py                 compatibility facade only
├── runtime_state.py           AgentDeps, ComposerDeps, AgentExecution,
│                              retrieval reservation enums/internal markers
├── agent_builder.py           bounded-autonomy instructions and build_agent()
├── agent_tools/
│   ├── __init__.py            registration exports only
│   ├── policy.py              prepare/gating, call accounting, bounded read
│   │                          recovery, shared tool execution helpers
│   ├── retrieval.py           search/get_neighbors/get_item/open_at
│   └── actions.py             always-on todo/save/pending/management tools
├── answer_pipeline.py         build_composer(), validators, evidence
│                              selection/rendering/fallback,
│                              answer-only repair execution
└── orchestrator.py            KnowledgeAgent and product run lifecycle
```

Existing focused modules remain unchanged owners of their domains:
`types.py`, `context.py`, `actions.py`, `autonomy.py`, `services.py`,
`provider.py`, and `answer_validation.py`.

## 4. Dependency direction

```text
types/context/actions/autonomy/services/provider/answer_validation
                              ↓
                         runtime_state
                              ↓
              agent_tools.policy / retrieval / actions
                              ↓
                         agent_builder

runtime_state + provider + answer_validation
                              ↓
                       answer_pipeline

agent_builder + answer_pipeline + runtime_state
                              ↓
                         orchestrator
                              ↓
                  runtime compatibility facade
```

`runtime_state` must not import builders, tools, the answer pipeline, or the
orchestrator. `agent_tools` must not import the orchestrator. This keeps import
cycles structurally impossible.

## 5. Responsibility details

### 5.1 `runtime_state.py`

Own the run-local data structures currently embedded in `runtime.py`:

- `RetrievalKind` and `ReservationResult`;
- `AgentDeps`, including locked reservation and citation recording;
- `ComposerDeps`;
- `AgentExecution`;
- small private recovery/result markers used across tool modules.

Security-sensitive scope matching may live here only if it is required by
`AgentDeps.record`; otherwise place it in `agent_tools.policy` and inject the
predicate explicitly. Do not create a dependency back to tool registration.

### 5.2 `agent_tools.policy`

Own cross-tool mechanics:

- dynamic `prepare_*` predicates;
- `execute_tool` accounting and privacy-safe diagnostic events;
- retrieval reservation and typed skipped payloads;
- bounded read failure projection and exact retry enforcement;
- management-read normalization;
- current-message reference validation shared by retrieval tools.

Represent this behavior as a small run-independent registration policy object
or a cohesive helper set. It receives per-run state only through
`RunContext[AgentDeps]`; it has no rollout-feature-flag branches.

### 5.3 Tool registration modules

Expose explicit registration functions, for example:

```python
register_retrieval_tools(agent, policy)
register_action_tools(
    agent,
    policy,
)
```

Decorated functions retain their current names and signatures so PydanticAI
produces unchanged tool schemas. Registration order should remain stable to
avoid changing model-facing tool order unnecessarily.

### 5.4 `agent_builder.py`

Own primary instructions, limits needed at Agent construction, and
`build_agent()`. It always constructs the bounded-autonomy PydanticAI Agent,
creates the shared tool policy, registers the complete tool set, and returns
the configured Agent. Its public signature no longer accepts
`management_enabled` or `bounded_autonomy_enabled`. It does not run the Agent
or finalize public answers.

### 5.5 `answer_pipeline.py`

Own the tool-free answering stage:

- `build_composer()` and its output validator;
- citation limiting, rendering, grouping, and source appending;
- evidence prompt rendering and deterministic fallback;
- Composer execution, bounded-answer repair, and evidence fallback.

Introduce an `AnswerPipeline` collaborator initialized from the answer model
and settings. It is invoked only for one bounded natural-answer repair and
deterministic evidence fallback; the legacy always-compose and compression
retry paths are removed. The trusted citation allow-list and fresh
repair-stage `RunUsage` semantics remain unchanged.

### 5.6 `orchestrator.py`

Own `KnowledgeAgent`. Refactor `run()` into named phases while preserving its
ordering:

1. parse current-message references and build action runtime;
2. take the deterministic bare-URL route when applicable;
3. build always-enabled action services and bounded `AgentDeps` including Todo
   and recovery state;
4. execute the primary Agent inside timeout, sequential-tool, and usage limits;
5. map primary-run exceptions without losing partial trusted evidence;
6. apply terminal action precedence;
7. finalize the bounded-autonomy natural answer or same-evidence repair.

Suggested private helpers include `_build_turn_dependencies`,
`_run_primary_agent`, `_handle_primary_failure`,
`_finalize_primary_result`, and `_terminal_action_execution`. Helpers may return
small internal result objects, but no model-authored discriminator may decide a
trusted finalization branch.

### 5.7 Compatibility facade

`runtime.py` re-exports the supported legacy surface:

```python
from .agent_builder import build_agent
from .answer_pipeline import (
    ComposerDeps,
    _append_sources,
    _compressed_citations,
    build_composer,
)
from .orchestrator import KnowledgeAgent
from .runtime_state import AgentDeps, AgentExecution
```

Add an explicit `__all__`. Tests may gradually switch private helper imports to
their owning module, but the facade remains compatible for this task.

## 6. Behavioral invariants

- The primary Agent remains `Agent[AgentDeps, str]` and always uses
  bounded-autonomy instructions and tool behavior.
- The Composer remains tool-free and uses `PromptedOutput(AnswerDraft)`.
- `parallel_tool_calls=False` remains advisory; the locked one-retrieval-per-
  run-step boundary remains authoritative.
- Tool names, descriptions, signatures, return payloads, `prepare` visibility,
  registration order, and timeout behavior remain unchanged.
- Terminal mutations and confirmations win over model prose and answer
  composition.
- There is no legacy planner-only output path or always-compose path.
- Save and item-management tools are always registered, but current-message
  scope, trusted pending state, confirmation codes, tenant predicates, action
  claims, and other server-side gates remain authoritative.
- Canonical history contains only the normalized question and final visible
  answer where currently allowed.
- Production diagnostics never gain questions, tool arguments/results,
  excerpts, URLs, IDs, drafts, or exception messages.

## 7. Migration strategy

Use incremental extractions with green tests between each step:

1. extract pure contracts and rendering helpers;
2. extract Composer repair and answer pipeline;
3. extract shared tool policy;
4. split retrieval and always-on action tool registration;
5. move/refactor `KnowledgeAgent` orchestration onto the bounded-only path;
6. remove the three settings/env flags from application composition, docs,
   evaluator preflight, and tests;
7. replace `runtime.py` with the compatibility facade;
8. update authoritative backend specs and run focused/full regression suites.

Avoid a single wholesale rewrite. Each extraction should preserve names and
call order, making review and rollback local.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Decorator registration changes tool schemas/order | Keep signatures, names, docstrings, prepare callbacks, and registration order; assert tool availability/schema tests |
| Circular imports between state, tools, and orchestration | Enforce the dependency graph above and run isolated import/compile checks |
| Removing the legacy path accidentally removes Composer repair | Keep `AnswerPipeline` repair/fallback tests and delete only unconditional legacy composition |
| Always-on writes weaken authorization | Preserve tenant, scope, pending confirmation, code, idempotency, and durable-claim checks; only remove composition flags |
| Exception handling order changes fallback semantics | Move exception branches mechanically before simplifying; run timeout/limit/provider/recovery tests after each phase |
| Private test imports break | Keep explicit compatibility re-exports and add a facade regression test |
| Security comments/invariants are lost during moves | Move invariant comments with the guarded code and review against backend specs |
| Unrelated dirty-tree changes are overwritten | Restrict edits to Agent runtime files/tests/specs and inspect the scoped diff before commit |

## 9. Rollback

Each extraction phase should be independently reviewable. If a phase changes
behavior, revert only that phase's module moves and facade exports; no database,
configuration, or migration rollback is required.
