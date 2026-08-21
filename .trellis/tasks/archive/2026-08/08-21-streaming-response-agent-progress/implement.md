# Implementation Plan

1. Inspect the existing conversation route, `Settings`, web API contracts, and
   ChatPage tests; choose the smallest compatible streaming transport and event
   schema.
2. Add `AGENT_STREAMING_ENABLED` to typed settings, `.env.example`, and config
   tests. Verify absent/true/false/invalid values.
3. Implement the authenticated streaming endpoint/client event parser while
   reusing `ChannelService.handle` and the existing safe answer projection.
4. Add controlled activity labels and terminal/error/cancel handling without
   exposing provider internals or hidden reasoning.
5. Update `web/src/chat/ChatPage.tsx`, API contracts/client helpers, and styles as
   needed so activity and answer text render incrementally and duplicate/stale
   events are harmless.
6. Add or update backend and frontend tests for event sequences, configuration,
   stream failure/cancellation, fallback/non-streaming behavior, and accessibility
   state transitions.
7. Run focused tests, typecheck/build, and the repository quality checks. Review
   the diff for auth/CSRF/tenant isolation, duplicate submissions, and sensitive
   data leakage before handoff.

## Validation commands

- `pytest -q <focused backend tests>`
- `npm test -- --run <focused frontend tests>` (or the repository's equivalent)
- `npm run build:web`
- `python3 ./.trellis/scripts/task.py validate 08-21-streaming-response-agent-progress`

## Rollback points

- Keep the existing JSON message route and client helper intact until streaming
  tests pass.
- If streaming transport integration is unsafe, disable it through
  `AGENT_STREAMING_ENABLED=false` and retain the JSON path while preserving the
  public event types for a later provider-level streaming implementation.
