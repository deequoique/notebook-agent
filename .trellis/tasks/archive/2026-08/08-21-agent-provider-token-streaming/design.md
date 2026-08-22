# Technical Design

## Existing boundary

The current SSE route in `app/api/conversation_routes.py` calls
`ChannelService.handle()` and emits one final `text_delta`. The channel service
owns idempotency, tenant resolution, locks, and durable `ConversationTurn`
storage. `KnowledgeAgent` owns bounded tool execution and the answer pipeline;
`AnswerPipeline`/the composer owns the trusted final text and citation projection.
The new work must preserve these ownership boundaries.

## Candidate streaming seam

Investigate the PydanticAI 2.15 streaming APIs already installed in the project:

- `Agent.run_stream(...)` + `StreamedRunResult.stream_text(delta=True)` for text
  output while retaining the final run result/messages.
- `Agent.run_stream_events(...)` only where event inspection is required; raw
  provider/model events remain internal.

The likely seam is the final composer stage: retrieval/tool execution must finish
and validate its evidence contract before answer text is public, while the
tool-free composer can stream its textual output. Keep a single in-flight
execution and expose an internal async event/callback channel from the Agent
runtime to the existing SSE adapter. Do not run a second `handle()` or a second
provider request for fallback.

## Safety and finalization

Buffer the provider stream and apply the existing trusted-response rules before
publishing a chunk. A chunk that might contain an unfinished citation/source
marker or URL must remain buffered until it is safe, or be withheld and replaced
by the final server-owned projection. The browser may see safe prose deltas, but
only the final `ConversationResponse` is authoritative and persisted.

At stream completion, run the same answer validation, citation projection, and
`save_completed_turn` path as non-streaming. If validation/provider/connection
failure occurs before a valid terminal answer, emit the existing safe error or
cancelled event and do not commit a partial turn. If a provider cannot stream,
use a single non-streaming call and emit one compatibility delta; the request
must not be retried through a second endpoint.

## Cancellation, backpressure, and observability

Use an async generator/context manager so disconnecting the HTTP response closes
the provider stream and cancels pending tasks. Bound any internal queue and avoid
an unbounded producer task. Preserve the configured total Agent timeout and
usage/tool limits. Log only request id, provider capability, chunk count, terminal
outcome, and elapsed time; never log chunk contents, prompts, credentials, or
provider response bodies.

## Frontend contract

The existing SSE parser and `ChatPage` delta append behavior remain the public
contract. Add only the client behavior/tests needed to demonstrate multiple
delayed deltas, terminal correction, and failure without duplicate submission.

## Runtime decision (2026-08-21)

The installed PydanticAI runtime exposes `Agent.run_stream()` and
`StreamedRunResult.stream_text()`, but the answer Composer currently returns a
structured `AnswerDraft` through `PromptedOutput` and a strict output
validator. `stream_text(delta=True)` explicitly bypasses output validators,
while partial `stream_output()` values are not trusted grounded sections and
can contain incomplete citation structure. Publishing those values would
weaken the existing citation and unsupported-section boundary. The current
task therefore keeps the bounded whole-answer compatibility path: after the
single `ChannelService.handle()` execution and final safe projection, SSE
emits at most one `text_delta` followed by the authoritative terminal
response. A future provider seam must stream only a separately validated safe
text output or introduce an equivalent final-section validator before changing
this decision.
