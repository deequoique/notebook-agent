# Modularize Agent runtime

## Goal

Refactor `app/agent/runtime.py` from a 2,451-line mixed-responsibility module
into cohesive Agent runtime modules, make bounded autonomy the only Agent
runtime, and make save and item-management capabilities permanently available
without environment feature flags.

## Requirements

### Functional requirements

- Remove the legacy flag-off planner-to-Composer runtime. The bounded-autonomy
  Turn Agent is the only primary Agent behavior.
- Remove `AGENT_BOUNDED_AUTONOMY_ENABLED` and
  `Settings.agent_bounded_autonomy_enabled`; there is no environment or
  programmatic switch back to the legacy runtime.
- Keep the Composer only as a tool-free, same-evidence answer repair and
  deterministic fallback component for bounded-autonomy validation failures.
- Remove `AGENT_SAVE_ENABLED` and `Settings.agent_save_enabled`; save,
  confirmation, and explicit ingestion retry capabilities are always composed
  and available subject to their existing server-owned validation.
- Remove `AGENT_ITEM_MANAGEMENT_ENABLED` and
  `Settings.agent_item_management_enabled`; inventory, update, delete,
  confirmation, restore, and retry Agent tools are always registered.
- Make same-origin Web submission/retry composition always enabled where it
  previously consumed `agent_save_enabled`.
- Preserve all retrieval budgets, tool schemas, dynamic tool visibility,
  sequential execution, recovery grants, timeout/usage handling, citation
  allow-list enforcement, answer composition, deterministic fallbacks, and
  terminal action precedence.
- Preserve tenant isolation, exact current-message reference scoping,
  pending-confirmation rules, mutation idempotency, and diagnostic redaction.
- Preserve the public response and persistence contracts: `AgentAnswer`,
  `AgentExecution`, canonical message history, citations, and action results.

### Modularization requirements

- Make `app/agent/runtime.py` a thin compatibility facade rather than the
  implementation owner.
- Separate at least these responsibilities:
  - run-scoped state and execution contracts;
  - primary Agent construction and instructions;
  - shared tool gating/execution/recovery policy;
  - retrieval/expansion tool registration;
  - save and item-management tool registration;
  - Composer construction, evidence rendering, one repair, and fallback;
  - product-level `KnowledgeAgent` orchestration and finalization.
- Enforce a one-way dependency direction so the new modules have no import
  cycles and tool modules do not depend on the orchestration layer.
- Keep named helpers small enough that the primary run lifecycle can be read as
  control flow rather than as hundreds of lines of inline policy branches.
- Preserve existing import compatibility from `app.agent.runtime`, including
  `KnowledgeAgent`, `AgentExecution`, `AgentDeps`, `ComposerDeps`,
  `build_agent`, `build_composer`, `_append_sources`, and
  `_compressed_citations`.
- Remove obsolete disabled-mode tests and replace them with always-on
  composition and tool-availability assertions. Preserve all remaining
  behavioral tests.
- Remove the three obsolete variables from `.env.example`, setup/deployment
  documentation, evaluator preflight, and operational instructions. Operators
  disable writes by stopping/isolating the appropriate runtime, not through
  removed Agent feature flags.
- Avoid unrelated feature work, budget changes, public payload schema changes,
  dependency upgrades, or broad test rewrites during the extraction.

### Maintainability constraints

- `app/agent/runtime.py` should contain only compatibility imports/exports and
  concise module documentation, with a target of at most 100 lines.
- No replacement module should become another 2,000-line catch-all; target a
  maximum of 700 lines per new implementation module.
- The main `KnowledgeAgent.run()` control-flow method should target at most 250
  lines by delegating construction, failure mapping, and answer finalization to
  named helpers.
- Comments that describe security, privacy, recovery, or concurrency
  invariants must move with the code they constrain.

## Acceptance Criteria

- [ ] `app/agent/runtime.py` is a compatibility facade of no more than 100
      lines and all previously imported runtime symbols remain available.
- [ ] Runtime state, tool policy, retrieval tools, action/management tools,
      answer composition, and orchestration have distinct implementation
      owners with an acyclic import graph.
- [ ] No new implementation module exceeds 700 lines and
      `KnowledgeAgent.run()` does not exceed 250 lines.
- [ ] `AGENT_BOUNDED_AUTONOMY_ENABLED`, `AGENT_SAVE_ENABLED`, and
      `AGENT_ITEM_MANAGEMENT_ENABLED` have no remaining runtime, settings,
      example-environment, evaluator, or documentation references.
- [ ] The primary Agent always uses bounded-autonomy instructions, Todo and
      recovery state, composable reads, natural-answer validation, and
      same-evidence Composer repair/fallback.
- [ ] Save and item-management action services are always composed; all save,
      pending, inventory, mutation, deletion, restore, and retry tools are
      registered and remain protected by existing trusted validation.
- [ ] Success, not-found, failure, action, citation, canonical-history, tenant,
      exact-reference, recovery, and diagnostic behavior remains correct on the
      single supported runtime path.
- [ ] Existing deterministic PydanticAI model-loop tests pass without real
      provider calls.
- [ ] Focused Agent, action, management, exact-reference, diagnostics,
      multi-user, MCP, and Web integration tests pass.
- [ ] The complete offline `pytest -q` suite passes.
- [ ] Python compilation/import checks and `git diff --check` pass.
- [ ] A regression test proves the compatibility facade exports the supported
      runtime surface.

## Notes

- This task intentionally removes three configuration fields/environment
  variables. It must not require a database migration, public payload change,
  or deployment data migration.
- The repository already contains unrelated uncommitted changes. Implementation
  must preserve them and limit edits to the Agent runtime and directly related
  tests/specs.
