# Knowledge Item Management Contract

## Scope

This contract applies to Agent tools and background jobs that list, inspect,
update, delete, restore, retry, or physically purge a tenant's saved knowledge
items. It also covers ingestion work that races with deletion or restoration.

## Public management boundary

- Every read and mutation is scoped by the resolved application tenant. Tool
  schemas never accept tenant, thread, pending-action, dispatch, or claim IDs.
- Inventory and detail tools return a bounded public projection. Internal
  object keys, purge claims, provider bodies, and model prose are never exposed.
- `update_saved_item` changes only `why_saved`. Trim input, normalize an empty
  string to `null`, enforce the public length bound, and keep identical writes
  idempotent.
- Management mutations and confirmations bypass retrieval composition and
  remain terminal canonical outcomes. Bounded autonomy is the only runtime;
  inventory/detail reads are non-terminal observations and may be followed by
  item-scoped knowledge retrieval in the same turn. A read-only-only turn still
  renders canonical server-owned inventory text. Persist only bounded public
  management context needed for safe follow-ups such as “next page” or “the
  second item”; item references never authorize a later read or write.
- Cursor tokens are versioned, tenant/filter-bound, length-bounded, and
  validated by the application. Never trust a model-authored cursor or ordinal
  without resolving it against persisted canonical context.

## Destructive confirmation

- A delete request snapshots validated item targets in durable server state.
  The confirmation tool never accepts item IDs.
- Every delete request receives a fresh server-generated confirmation code.
  The code is shown to the user, stored only as a hash, parsed from the raw
  current user message by the service, and required for every confirmation.
  Plain “yes,” an old code, missing hashes, and legacy or forged flags fail
  closed.
- Clarification, an incorrect code, or a recoverable effect failure advances a
  server-owned confirmation anchor so the next turn can continue the same
  action without making delayed replies valid for a replacement action.
- External delete execution uses an exclusive durable claim token and an
  `applying / failed / applied` state machine. A consumed action must always
  have a persisted canonical result; “effect unknown” is never reported as
  success.
- Once an action is `applying`, the user-confirmation TTL no longer cancels it.
  Recovery uses a separate effect lease. Replacement delete/save requests,
  cancellation, and `/new` fail closed until the effect completes or a stale
  lease is reclaimed.
- Restore and re-save replace the item's delete fence. A late delete effect
  then becomes an explicit `already_restored` no-op, and the canonical response
  reports per-item `deleted`, `already_deleted`, and `already_restored` results
  accurately.

## Recycle bin, retrieval, and ingestion

- `archived_at` and `deleted_at` are separate product states. Archive is a
  reversible Web visibility choice that keeps segments and raw objects;
  deletion enters the recycle-bin and always takes precedence when both fields
  are non-null. Active Agent inventory, vector/BM25 hydration, neighbors,
  detail, and open-at paths require both fields to be null. Trash inventory may
  show deleted rows regardless of their former archive state.
- Web detail, transcript, archive/unarchive, dispatch, and retry queries must
  apply the tenant predicate and `deleted_at IS NULL` independently. Restoring
  from trash or re-saving the same URL clears both deletion and archive state,
  so a successful restore cannot remain hidden in the archive view.
- Soft deletion sets `deleted_at` using PostgreSQL time. All vector, BM25,
  hydration, neighbor, detail, and open-at retrieval paths require
  `deleted_at IS NULL`. Management tools are always composed; these gates are
  independent defense in depth.
- Re-saving the same URL during retention restores the existing row. A new
  non-null `why_saved` replaces the old value; null preserves it.
- Explicit retry is accepted only for a stable failed active item with no
  active dispatch. Dispatch and item failure after broker publication failure
  are committed atomically; retry admission repairs the known legacy
  `pending item + queue_unavailable dispatch` split state.
- The ingestion worker persists a deterministic raw-object cleanup intent
  before upload. If deletion wins a race, item and dispatch converge to
  `failed / item_deleted`; restore or re-save can then create the next dispatch
  instead of leaving a permanent `chunking` item.

## Physical purge

- Retention, claim eligibility, stale-claim recovery, and final deletion use
  PostgreSQL time. Claims are batch bounded and use `FOR UPDATE SKIP LOCKED`.
- The whole sweep has a wall-clock budget beginning before count/claim work.
  PostgreSQL work uses a remaining-time `statement_timeout` via parameterized
  `set_config`, and object deletion receives a per-call budget smaller than the
  remaining sweep time.
- Object-store adapters used by purge must expose an inspectable,
  timeout-capable delete signature. Select the compatible call shape before
  invoking it. Never catch an adapter's internal `TypeError` and retry without
  a timeout.
- Dynamic object clients allow exactly one total attempt and split connect and
  read timeouts within the supplied budget. Missing objects are success;
  provider failures remain retryable and never expose keys or response bodies.
- When the deadline is exhausted, do not start unbounded cleanup SQL. Leave the
  claim for bounded claim-timeout recovery and emit privacy-safe counters with
  `timed_out` and `timeout_phase`.
- Downgrade must refuse while soft-deleted rows exist. Operational rollback
  requires gateway/Web isolation plus Beat shutdown, a backup, an explicit
  restore-or-purge decision, migration verification, and retrieval smoke tests.
- When independently shipped feature migrations branch from the same released
  revision, preserve both historical files and add a no-op Alembic merge
  revision. Never rewrite a deployed sibling migration or re-parent another
  branch after environments may already have applied it.

## Required validation

- Exercise tenant isolation for every operation, cursor/filter binding,
  destructive confirmation replacement/cancel/consume/expiry races, duplicate
  delivery, effect lease recovery, and canonical cross-turn continuation.
- Cover save auto-restore, broker failure and split-state retry repair, worker
  delete-to-restore convergence, archived/deleted precedence, active and trash
  visibility, deleted-content Web/Agent gates, merge-head topology, purge claim
  recovery, statement timeout SQL, object adapter capability, and deadline
  diagnostics.
- Run SQLite/offline tests for deterministic state transitions and real
  PostgreSQL/HTTP integration before release. Validate MinIO timeout and race
  behavior in a disposable environment when the local runtime is unavailable.
