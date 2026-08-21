# Technical Design

## Scope and boundaries

The canonical browser conversation surface is `app/api/conversation_routes.py`
and `web/src/chat/`. Keep the existing JSON `POST /api/v1/conversations/{conversation_id}/messages`
contract intact for non-streaming clients, and add a browser-safe streaming path
that reuses the same authenticated session, CSRF, tenant, channel-service, and
answer projection boundaries.

The stream may expose only typed public events and controlled Chinese activity
labels. It must not forward provider chunks, tool arguments, raw diagnostics,
model messages, or hidden reasoning.

## Configuration

Add a typed `Settings` boolean backed by `AGENT_STREAMING_ENABLED`, using the
existing `_env_bool` parser. The default is `True`; invalid values fail during
configuration loading. Add the variable and default to `.env.example` and keep
the setting injectable in tests and lightweight app compositions.

## Event contract

Use a documented, browser-consumable event format (SSE is acceptable if it fits
the existing FastAPI stack). Every event carries a request/message correlation
identifier and a monotonic sequence. The public event types must cover start,
activity, text delta, complete, error, and cancelled. Complete/error/cancelled
events are terminal and contain the final safe `ConversationResponse` projection
or a safe error summary as appropriate. Duplicate or stale sequence numbers are
ignored by the client; a disconnect has a deterministic error/fallback state.

The implementation may use the existing whole-answer `ChannelService.handle`
as the compatibility execution path. If provider-level token streaming is not
available, emit safe activity events and one final text delta before completion;
the protocol and UI must still be incremental and remain correct. Do not invent
an unbounded background task or a second persistence path.

## Frontend behavior

Add a streaming client helper with injectable fetch/stream parsing for tests.
`ChatPage` should select the streaming path when the server setting/capability
allows it, render activity labels while pending, append text deltas exactly once,
and replace the pending turn with the final persisted-safe response on terminal
completion. On stream failure, show a bounded user-facing error and either use
the existing JSON request as a one-time fallback or leave the request in a clear
failed state; never submit the same message twice silently.

Maintain accessible live status (`aria-live`/`aria-busy`) and preserve existing
history invalidation after completion. Keep the first-empty bootstrap and reset
flows non-streaming unless the chosen design can prove the same response shape.

## Compatibility and observability

The existing JSON route remains unchanged and is used when
`AGENT_STREAMING_ENABLED=false` or by clients that do not opt into streaming.
Log bounded lifecycle events (request id, event type, terminal outcome, elapsed
time) without response content or secrets. Add focused backend and frontend tests
for configuration, event ordering/projection, terminal states, duplicate/stale
events, stream failure/cancellation, and non-streaming compatibility.
