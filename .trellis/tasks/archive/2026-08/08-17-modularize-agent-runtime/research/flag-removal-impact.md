# Always-on Agent capability impact map

## Removed configuration

- `AGENT_BOUNDED_AUTONOMY_ENABLED` /
  `Settings.agent_bounded_autonomy_enabled`
- `AGENT_SAVE_ENABLED` / `Settings.agent_save_enabled`
- `AGENT_ITEM_MANAGEMENT_ENABLED` /
  `Settings.agent_item_management_enabled`

## Runtime owners affected

- `app/agent/runtime.py`: dual orchestration, Agent builder flags, action runtime
  enablement, management registration, Todo/recovery construction, and answer
  finalization.
- `app/bootstrap.py`: conditional `AgentActionServices` composition.
- `app/channels/service.py`: conditional `ContextBuilder` composition.
- `app/api/runtime.py`: Web library and route save enablement.
- `app/web_api.py`: compatibility Web save enablement.
- `app/config.py`: fields, environment parsing, and validation.
- `evals/natural_language/runner.py`: obsolete always-true preflight.

## Repository contracts affected

- `.env.example`
- `.trellis/spec/backend/agent-retrieval-convergence.md`
- `.trellis/spec/backend/knowledge-item-management.md`
- deployment and getting-started documentation
- natural-language evaluator README

## Test impact

Tests currently construct enabled and disabled combinations across Agent,
bootstrap, Web, MCP, and evaluator suites. Disabled-mode expectations must be
removed or changed to assert always-on composition. Behavioral tests for
confirmation, tenant isolation, exact reference scope, recovery, diagnostics,
idempotency, and canonical persistence remain required.

## Security boundary

Removing composition flags does not remove authorization or safety checks.
Save and management operations remain constrained by authenticated tenant,
current-message URL matching, trusted pending state, confirmation codes,
bounded schemas, durable claims, idempotency, and domain-service predicates.
