# Modularize and unify Agent runtime — implementation plan

## 1. Establish the compatibility and behavior baseline

- [x] Record the current `app.agent.runtime` import surface used by application
      code and tests, including private helper imports.
- [x] Add a focused compatibility-surface regression test before replacing the
      module implementation.
- [x] Run the focused Agent suites to establish a green baseline without real
      provider calls.
- [x] Confirm the working tree's unrelated changes and keep them outside the
      scoped diff.

## 2. Extract run state and pure answer helpers

- [x] Create `runtime_state.py` and move run-scoped enums, data classes,
      reservation logic, citation recording, and execution contracts without
      semantic changes.
- [x] Create `answer_pipeline.py` and first move pure citation limiting,
      evidence rendering, source rendering, and compatibility helpers.
- [x] Preserve `_append_sources` and `_compressed_citations` compatibility
      exports from `runtime.py`.
- [x] Run pure helper, retrieval convergence, and import tests.

## 3. Extract Composer and answer-only execution

- [x] Move `build_composer`, Composer instructions, output validation, and
      Composer constants into `answer_pipeline.py`.
- [x] Introduce `AnswerPipeline` to own one same-evidence repair,
      deterministic fallback, and answer-phase diagnostics; do not retain the
      legacy unconditional composition or compression-retry paths.
- [x] Keep fresh `RunUsage`, timeout, output-token, and citation allow-list
      semantics unchanged.
- [x] Run Composer/provider, citation, timeout, and fallback tests.

## 4. Split tool policy and registration

- [x] Create `agent_tools/policy.py` for dynamic tool visibility, tool-call
      accounting, retrieval reservation, read recovery, and shared validation.
- [x] Create `agent_tools/retrieval.py` for `search_segments`,
      `get_neighbors`, `get_item`, and `open_at` registration.
- [x] Create `agent_tools/actions.py` for always-registered `todo_write`,
      save/pending tools, and item-management tools.
- [x] Preserve every tool name, signature, description, return payload,
      registration order, prepare predicate, retry behavior, and terminal
      action semantics.
- [x] Create `agent_builder.py` to own bounded-autonomy instructions,
      primary-Agent construction, policy composition, and full tool
      registration; remove builder feature-flag parameters.
- [x] Run Agent-loop, batched-tool, recovery, save/pending, and item-management
      suites.

## 5. Remove rollout flags and simplify product orchestration

- [x] Move `KnowledgeAgent` to `orchestrator.py`.
- [x] Remove the legacy planner/Composer mode and every
      `agent_bounded_autonomy_enabled` branch. Always create bounded Todo and
      recovery state and always apply natural-answer validation.
- [x] Remove `agent_save_enabled` and `agent_item_management_enabled` branches.
      Always compose `AgentActionServices`, enable save behavior, enable
      composable management reads, and register the complete management tool
      set.
- [x] Split dependency construction, primary-Agent execution, exception
      mapping, terminal action handling, and answer finalization into named
      helpers while preserving branch order.
- [x] Delegate answer-only behavior to `AnswerPipeline`.
- [x] Keep deterministic bare-URL routing, exact-reference scope, partial
      trusted evidence, canonical history, and diagnostic phases unchanged.
- [x] Reduce `KnowledgeAgent.run()` to at most 250 lines.
- [x] Remove the three Settings fields and their environment parsing and
      validation; remove the variables from `.env.example`.
- [x] Make Web/API save composition always enabled and remove evaluator
      preflight checks for the deleted flags.
- [x] Update setup/deployment/evaluator documentation so it no longer instructs
      operators to set or toggle removed variables.
- [x] Replace disabled-mode tests with bounded-only and always-on capability
      assertions.
- [x] Run bounded-autonomy, exact-reference, diagnostics, multi-user, channel
      persistence, MCP, Web, configuration, and evaluator suites.

## 6. Install the compatibility facade and review structure

- [x] Replace `app/agent/runtime.py` with an explicit compatibility facade of
      at most 100 lines.
- [x] Export `KnowledgeAgent`, `AgentExecution`, `AgentDeps`, `ComposerDeps`,
      `build_agent`, `build_composer`, `_append_sources`, and
      `_compressed_citations`.
- [x] Verify no implementation module exceeds 700 lines and the import graph
      follows `design.md` without cycles.
- [x] Review the scoped diff for accidental behavior edits, lost invariant
      comments, and unrelated working-tree changes.

## 7. Validation gates

- [x] Compile/import gate:

      ```bash
      .venv/bin/python -m compileall -q app/agent
      .venv/bin/python -c "import app.agent.runtime"
      ```

- [x] Focused Agent regression gate:

      ```bash
      .venv/bin/pytest -q \
        tests/test_agent_runtime.py \
        tests/test_bounded_autonomy_runtime.py \
        tests/test_bounded_recovery_runtime.py \
        tests/test_agent_actions.py \
        tests/test_item_management_tools.py \
        tests/test_exact_video_reference_routing.py \
        tests/test_provider_and_explicit_user.py
      ```

- [x] Cross-boundary regression gate:

      ```bash
      .venv/bin/pytest -q \
        tests/test_multiuser_integration.py \
        tests/test_diagnostics.py \
        tests/test_mcp_server.py \
        tests/test_web_email_auth.py
      ```

- [x] Full offline suite: `.venv/bin/pytest -q` (socket tests run separately
      outside the filesystem/network sandbox).
- [x] Diff hygiene: `git diff --check`.
- [x] Trellis context/task validation:
      `python3 ./.trellis/scripts/task.py validate 08-17-modularize-agent-runtime`.

## 8. Review and rollback gates

- [x] After each extraction phase, stop if focused tests expose a behavior
      difference; fix or revert that phase before continuing.
- [x] Do not combine dependency upgrades, model changes, schema changes,
      configuration changes, or unrelated cleanup with this refactor.
- [x] Before completion, prove the removed environment/settings names have no
      remaining repository references and review terminal action paths against
      the PRD invariants.
