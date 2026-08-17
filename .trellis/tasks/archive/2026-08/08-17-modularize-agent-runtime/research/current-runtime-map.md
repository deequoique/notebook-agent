# Current Agent runtime map

## Current size and ownership

- `app/agent/runtime.py`: 2,451 lines.
- Prompts and policy constants: approximately lines 76–233.
- Run state/contracts: approximately lines 234–345.
- Primary Agent construction and all tools: approximately lines 346–1328.
- Composer construction: approximately lines 1331–1389.
- `KnowledgeAgent` lifecycle: approximately lines 1392–2287.
- Citation rendering/compression/fallback helpers: approximately lines
  2288–2451.

## Existing runtime import surface

Application code imports:

- `KnowledgeAgent` from `app.agent.runtime` in bootstrap, channel service, and
  `app.agent.__init__`.

Tests additionally import:

- `AgentExecution`;
- `AgentDeps`;
- `ComposerDeps`;
- `build_agent`;
- `build_composer`;
- `_append_sources`;
- `_compressed_citations`.

The compatibility facade must retain these names during the refactor.

## High-value regression suites

- `tests/test_agent_runtime.py`
- `tests/test_bounded_autonomy_runtime.py`
- `tests/test_bounded_recovery_runtime.py`
- `tests/test_agent_actions.py`
- `tests/test_item_management_tools.py`
- `tests/test_exact_video_reference_routing.py`
- `tests/test_provider_and_explicit_user.py`
- `tests/test_multiuser_integration.py`
- `tests/test_diagnostics.py`
- `tests/test_mcp_server.py`
- `tests/test_web_email_auth.py`

These cover the real PydanticAI loop, tool schemas and visibility, terminal
actions, recovery, reference scope, tenant isolation, provider settings,
diagnostic privacy, persistence, and compatibility consumers.
