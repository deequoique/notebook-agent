# Browser companion pairing failure investigation design

## Diagnostic boundary

Trace one pairing as a state machine:

```text
extension create -> DB pending -> Web approve -> DB approved
  -> extension status -> exchange -> DB used + one grant -> device list
```

Each transition receives a safe correlation record containing only request ID, operation name, pairing public ID, Origin classification, HTTP outcome, database state transition, and exception class/SQLSTATE when applicable. Raw verifier, challenge material, token hashes, user IDs, and credentials are excluded.

## Evidence strategy

1. Identify the exact unpacked directory currently intended for the local build and derive its manifest version, API Origin, host permission, and fixed manifest key from files on disk.
2. Add operation-specific server diagnostics around create/status/exchange, including a sanitized database failure classification. Keep the verifier entirely outside logs.
3. Ensure popup errors preserve stable server codes and distinguish local transport failures from HTTP failures.
4. Before the user performs one controlled attempt, record the current latest pairing and grant count. After the attempt, correlate server logs and database state by pairing public ID and request ID.
5. Fix only the proven boundary. If remote PostgreSQL reliability is causal, harden connection/retry behavior only where transaction semantics make retry safe; never blindly replay a possibly committed exchange.

## Confirmed root cause

Chrome MV3 omits Origin on the service-worker pairing-status GET. The current middleware rejects that safe read before it reaches the status route, while the create POST includes Origin and succeeds. The minimal design permits missing Origin only on that exact read-only status route. All state-changing extension routes retain Origin enforcement.

## Security decisions

- Do not weaken PKCE to make exchange succeed.
- Do not expose the verifier through popup UI, logs, browser automation, or database queries.
- Do not accept ordinary `http(s)` Origins under the development wildcard.
- Do not retry exchange after an ambiguous commit without first checking the pairing/grant transaction outcome.
- Diagnostics use public pairing IDs and request IDs only.

## Rollback

- Diagnostic logging is isolated and can be removed without schema changes.
- The development wildcard is controlled by `.env`; production exact-Origin behavior remains the default.
- Popup copy/recovery changes are contained within the extension build and can be reverted independently from backend changes.
