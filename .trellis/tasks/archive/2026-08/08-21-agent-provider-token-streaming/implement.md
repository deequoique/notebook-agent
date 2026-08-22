# Implementation Plan

1. Read the parent task's SSE contract and the backend Agent/answer-pipeline
   specs; inventory PydanticAI 2.15 streaming APIs and provider capability
   differences.
2. Add focused fake-streaming provider/model fixtures and tests first to prove
   multiple delayed deltas, cancellation, final result retrieval, and fallback
   behavior without network access.
3. Introduce the smallest internal runtime seam for streaming the final safe
   answer while retaining the existing non-streaming `KnowledgeAgent.run()` and
   `ChannelService.handle()` compatibility path.
4. Thread safe deltas into the existing SSE route. Ensure final validation,
   citation/source projection, persistence, request locking, timeout, and
   idempotency happen exactly once.
5. Add content-boundary tests proving hidden reasoning, provider events, tool
   arguments, URLs, and unfinished citation/source markers never leak through
   public deltas.
6. Add frontend tests for two or more delayed deltas, terminal replacement,
   out-of-order/duplicate handling, stream abort, and non-streaming fallback.
7. Run focused backend/provider/frontend tests, full typecheck/build/lint and
   OpenAPI checks; then review for duplicate provider calls, leaked content,
   uncancelled tasks, and persistence divergence.

### Current runtime boundary

The provider API inspection confirmed that `run_stream()` is available, but
the production Composer is a structured `AnswerDraft` with a strict
grounded/unsupported validator. Its partial stream is not a safe public text
source, and `stream_text(delta=True)` bypasses the validator. Keep the
one-delta SSE compatibility path until a provider-specific final-text seam is
available; tests must prove no second `ChannelService.handle()` invocation,
no partial persistence, and safe terminal correction in the meantime.

## Validation commands

- `.venv/bin/pytest -q <focused provider/runtime/SSE tests>`
- `PATH=<bundled-node>:$PATH pnpm --dir web test`
- `PATH=<bundled-node>:$PATH pnpm --dir web run typecheck`
- `PATH=<bundled-node>:$PATH pnpm --dir web run build`
- `.venv/bin/python scripts/export_web_openapi.py --check`
- `python3 ./.trellis/scripts/task.py validate 08-21-agent-provider-token-streaming`

## Rollback points

- Keep `AGENT_STREAMING_ENABLED=false` as an operational kill switch.
- Preserve the existing whole-answer SSE compatibility path until provider-level
  streaming passes all safety and persistence tests.
- If a provider cannot satisfy safe incremental output, leave it on the one-delta
  compatibility path rather than weakening the trusted response boundary.
