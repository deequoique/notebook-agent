"""Durable, tenant-bound pending channel action lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re
import secrets
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.ingest.submission import normalize_item_reference, prepare_submission
from app.models import ConversationThread, PendingChannelAction

if TYPE_CHECKING:
    from app.agent.management import BatchItemOperationResult, KnowledgeItemManagementService


@dataclass(frozen=True)
class ConfirmationResult:
    status: Literal[
        "confirmation_required",
        "confirmed",
        "cancelled",
        "confirmation_missing",
        "confirmation_expired",
        "effect_failed",
        "effect_in_progress",
    ]
    urls: tuple[str, ...] = ()
    item_ids: tuple[int, ...] = ()
    results: tuple[dict, ...] = ()
    error_code: str | None = None
    action_id: int | None = None
    # Server-owned origin thread retained through confirmation.  This is an
    # internal routing value and is never part of model/tool arguments.
    thread_id: int | None = None
    replayed: bool = False
    # Only request responses carry this one-time display value.  Confirmation
    # tools never accept it as an argument; the service parses the raw user
    # message and compares its hash to the trusted pending row.
    confirmation_code: str | None = None


@dataclass(frozen=True)
class PendingSaveSnapshot:
    """Minimal server-owned state that may be shown to the Agent."""

    active: bool
    count: int = 0


@dataclass(frozen=True)
class PendingDeleteSnapshot:
    active: bool
    count: int = 0
    requires_code: bool = False


class PendingValidationError(ValueError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class PendingConfirmationService:
    """Persist and atomically consume one save batch per conversation."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("pending confirmation TTL must be positive")
        self._session_factory = session_factory
        self._ttl = ttl
        # A committed ``applying`` claim survives process crashes.  A fresh
        # claim is never executed twice; once this short lease expires a new
        # confirmation may recover the operation idempotently.
        self._effect_claim_timeout = min(ttl, timedelta(minutes=1))

    def request_save(
        self,
        tenant: TenantContext,
        thread_id: int,
        urls: list[str],
    ) -> ConfirmationResult:
        canonical_urls = self._canonical_urls(urls)
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            now = db.scalar(select(func.now()))
            now = self._as_utc(now)
            current = self._active(db, thread.id)
            if self._delete_effect_applying(current):
                # A save request is also a replacement on this conversation;
                # never cancel a delete action after its external effect has
                # claimed ownership of the item rows.
                return ConfirmationResult(
                    "effect_in_progress",
                    error_code="delete_in_progress",
                )
            if current is not None:
                current.cancelled_at = now
                db.flush()
            action = PendingChannelAction(
                thread_id=thread.id,
                kind="save_videos",
                payload={"version": 1, "urls": list(canonical_urls)},
                expires_at=now + self._ttl,
            )
            db.add(action)
            db.flush()
            action_id = action.id
            db.commit()
        return ConfirmationResult(
            "confirmation_required",
            urls=canonical_urls,
            action_id=action_id,
        )

    def confirm_save(
        self,
        tenant: TenantContext,
        thread_id: int,
        *,
        message_id: str,
    ) -> ConfirmationResult:
        if not message_id.strip():
            raise ValueError("message id is required")
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            replay = db.scalar(
                select(PendingChannelAction)
                .where(
                    PendingChannelAction.thread_id == thread.id,
                    PendingChannelAction.kind == "save_videos",
                    PendingChannelAction.consumed_message_id == message_id,
                    PendingChannelAction.consumed_at.is_not(None),
                )
                .order_by(PendingChannelAction.id.desc())
                .limit(1)
            )
            if replay is not None:
                urls = self._payload_urls(replay)
                if urls is None:
                    return ConfirmationResult("confirmation_missing")
                return ConfirmationResult(
                    "confirmed",
                    urls=urls,
                    action_id=replay.id,
                    thread_id=replay.thread_id,
                    replayed=True,
                )

            current = self._active(db, thread.id, kind="save_videos")
            if current is None:
                return ConfirmationResult("confirmation_missing")
            now = self._as_utc(db.scalar(select(func.now())))
            if self._as_utc(current.expires_at) <= now:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_expired")
            urls = self._payload_urls(current)
            if urls is None:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_missing")
            current.consumed_at = now
            current.consumed_message_id = message_id
            db.commit()
            return ConfirmationResult(
                "confirmed",
                urls=urls,
                action_id=current.id,
                thread_id=current.thread_id,
            )

    def cancel_save(
        self,
        tenant: TenantContext,
        thread_id: int,
    ) -> ConfirmationResult:
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            current = self._active(db, thread.id, kind="save_videos")
            if current is None:
                return ConfirmationResult("confirmation_missing")
            now = self._as_utc(db.scalar(select(func.now())))
            if self._as_utc(current.expires_at) <= now:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_expired")
            current.cancelled_at = now
            db.commit()
            return ConfirmationResult(
                "cancelled",
                action_id=current.id,
            )

    def inspect_save(
        self,
        tenant: TenantContext,
        thread_id: int,
    ) -> PendingSaveSnapshot:
        """Return a read-only, tenant-bound summary of a live save batch.

        This deliberately does not reuse the consuming path: it neither locks
        rows nor writes expiry/cancellation timestamps, and never exposes the
        persisted URLs or action identifier to the caller.
        """

        try:
            with self._session_factory() as db:
                action = db.scalar(
                    select(PendingChannelAction)
                    .join(
                        ConversationThread,
                        PendingChannelAction.thread_id
                        == ConversationThread.id,
                    )
                    .where(
                        ConversationThread.id == thread_id,
                        ConversationThread.app_user_id
                        == tenant.app_user_id,
                        ConversationThread.channel_identity_id
                        == tenant.channel_identity_id,
                        ConversationThread.closed_at.is_(None),
                        PendingChannelAction.kind == "save_videos",
                        PendingChannelAction.consumed_at.is_(None),
                        PendingChannelAction.cancelled_at.is_(None),
                        PendingChannelAction.expires_at > func.now(),
                    )
                    .order_by(PendingChannelAction.id.desc())
                    .limit(1)
                )
                urls = self._payload_urls(action) if action is not None else None
                if urls is None:
                    return PendingSaveSnapshot(active=False)
                return PendingSaveSnapshot(active=True, count=len(urls))
        except Exception:
            # This context is advisory only. A database failure must not leak
            # pending data or make an unverified batch actionable.
            return PendingSaveSnapshot(active=False)

    @staticmethod
    def _lock_thread(
        db: Session,
        tenant: TenantContext,
        thread_id: int,
    ) -> ConversationThread | None:
        return db.scalar(
            select(ConversationThread)
            .where(
                ConversationThread.id == thread_id,
                ConversationThread.app_user_id == tenant.app_user_id,
                ConversationThread.channel_identity_id
                == tenant.channel_identity_id,
                ConversationThread.closed_at.is_(None),
            )
            .with_for_update()
        )

    def request_delete(
        self,
        tenant: TenantContext,
        thread_id: int,
        item_ids: list[int] | tuple[int, ...],
        *,
        management: "KnowledgeItemManagementService",
        request_message_id: str | None = None,
        latest_turn_message_id: str | None = None,
    ) -> ConfirmationResult:
        """Validate targets, then persist only versioned internal ids."""

        targets = management.request_delete_targets(tenant, item_ids)
        confirmation_code = self._new_confirmation_code()
        code_hash = self._hash_confirmation_code(confirmation_code)
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            now = db.scalar(select(func.now()))
            current = self._active_any(db, thread.id)
            if self._delete_effect_applying(current):
                # Do not replace an action whose external effect is already
                # running. Replacing it would let the old worker finalize a
                # cancelled row (or mutate an item after a newer request),
                # defeating the durable claim fence.
                return ConfirmationResult(
                    "effect_in_progress",
                    error_code="delete_in_progress",
                )
            # Every delete request displays a one-time code. A raw “yes” is
            # never enough: channel deliveries have no trusted reply-to
            # metadata, so accepting plain text could consume a delayed
            # confirmation for a different target batch.
            requires_code = True
            if current is not None:
                current.cancelled_at = now
                db.flush()
            action = PendingChannelAction(
                thread_id=thread.id,
                kind="delete_saved_items",
                payload={
                    "version": 1,
                    "item_ids": list(targets),
                    "kind": "delete_saved_items",
                    "confirmation_code_hash": code_hash,
                    "requires_code": requires_code,
                    "request_message_id": request_message_id,
                    "confirmation_anchor_message_id": request_message_id,
                    "confirmation_anchor_parent_message_id": latest_turn_message_id,
                    "effect_state": "pending",
                },
                expires_at=now + self._ttl,
            )
            db.add(action)
            db.flush()
            action_id = action.id
            db.commit()
        return ConfirmationResult(
            "confirmation_required",
            item_ids=targets,
            action_id=action_id,
            confirmation_code=confirmation_code,
        )

    def inspect_delete(self, tenant: TenantContext, thread_id: int) -> PendingDeleteSnapshot:
        try:
            with self._session_factory() as db:
                action = db.scalar(
                    select(PendingChannelAction)
                    .join(ConversationThread, PendingChannelAction.thread_id == ConversationThread.id)
                    .where(
                        ConversationThread.id == thread_id,
                        ConversationThread.app_user_id == tenant.app_user_id,
                        ConversationThread.channel_identity_id == tenant.channel_identity_id,
                        ConversationThread.closed_at.is_(None),
                        PendingChannelAction.kind == "delete_saved_items",
                        PendingChannelAction.consumed_at.is_(None),
                        PendingChannelAction.cancelled_at.is_(None),
                    )
                    .order_by(PendingChannelAction.id.desc())
                    .limit(1)
                )
                # Keep the latest unresolved row visible even after its
                # confirmation TTL. The decision tool accepts no target IDs,
                # and the mutation service remains the authority that returns
                # ``confirmation_expired`` and cancels the row. Hiding it here
                # would turn a valid expired-code reply into an unknown-tool
                # runtime error before the trusted service can classify it.
                ids = self._payload_item_ids(action) if action is not None else None
                return PendingDeleteSnapshot(
                    active=bool(ids),
                    count=len(ids or ()),
                    requires_code=True,
                )
        except Exception:
            return PendingDeleteSnapshot(active=False)

    def confirm_delete(
        self,
        tenant: TenantContext,
        thread_id: int,
        *,
        message_id: str,
        message_text: str,
        management: "KnowledgeItemManagementService",
        latest_turn_message_id: str | None = None,
    ) -> "tuple[ConfirmationResult, BatchItemOperationResult | None]":
        if not message_id.strip():
            raise ValueError("message id is required")
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing"), None
            replay = db.scalar(
                select(PendingChannelAction)
                .where(
                    PendingChannelAction.thread_id == thread.id,
                    PendingChannelAction.kind == "delete_saved_items",
                    PendingChannelAction.consumed_message_id == message_id,
                    PendingChannelAction.consumed_at.is_not(None),
                )
                .order_by(PendingChannelAction.id.desc())
                .limit(1)
            )
            if replay is not None:
                ids = self._payload_item_ids(replay)
                if ids is None:
                    return ConfirmationResult("confirmation_missing"), None
                stored = self._payload_results(replay)
                return ConfirmationResult(
                    "confirmed", item_ids=ids, action_id=replay.id,
                    results=stored, replayed=True,
                ), None
            current = self._active(db, thread.id, kind="delete_saved_items")
            if current is None:
                return ConfirmationResult("confirmation_missing"), None
            now = self._as_utc(db.scalar(select(func.now())))
            payload = dict(current.payload) if isinstance(current.payload, dict) else {}
            effect_state = payload.get("effect_state", "pending")
            if effect_state != "applying" and self._as_utc(current.expires_at) <= now:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_expired"), None
            ids = self._payload_item_ids(current)
            if ids is None:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_missing"), None
            if not self._request_is_current(
                payload,
                latest_turn_message_id=latest_turn_message_id,
            ):
                # The action request must be the newest completed turn.  This
                # is an additional server-side guard; replacement requests
                # also require the raw confirmation code below.
                return ConfirmationResult("confirmation_missing"), None
            # Every delete row requires a code, including legacy rows that
            # predate the explicit ``requires_code`` flag and rows carrying a
            # stale/false flag.  Such rows have no trusted hash and therefore
            # fail closed instead of silently restoring the old plain-yes
            # path.
            supplied_code = self._extract_confirmation_code(message_text)
            expected_hash = payload.get("confirmation_code_hash")
            if not supplied_code or not isinstance(expected_hash, str):
                self._advance_confirmation_anchor(
                    payload,
                    message_id=message_id,
                    latest_turn_message_id=latest_turn_message_id,
                )
                current.payload = payload
                db.commit()
                return ConfirmationResult("confirmation_missing"), None
            if not secrets.compare_digest(
                self._hash_confirmation_code(supplied_code), expected_hash
            ):
                self._advance_confirmation_anchor(
                    payload,
                    message_id=message_id,
                    latest_turn_message_id=latest_turn_message_id,
                )
                current.payload = payload
                db.commit()
                return ConfirmationResult("confirmation_missing"), None
            effect_state = payload.get("effect_state", "pending")
            if effect_state == "applying":
                claim_at = self._payload_datetime(payload.get("effect_claimed_at"))
                if claim_at is None or claim_at + self._effect_claim_timeout > now:
                    self._advance_confirmation_anchor(
                        payload,
                        message_id=message_id,
                        latest_turn_message_id=latest_turn_message_id,
                    )
                    current.payload = payload
                    db.commit()
                    return ConfirmationResult(
                        "effect_in_progress", item_ids=ids, action_id=current.id,
                        error_code="delete_in_progress",
                    ), None
                # The old worker lease is stale. Re-arm the action TTL while
                # this confirmation takes ownership; item delete fences still
                # make a late old worker a no-op.
                current.expires_at = now + self._ttl
            self._advance_confirmation_anchor(
                payload,
                message_id=message_id,
                latest_turn_message_id=latest_turn_message_id,
            )
            claim_token = secrets.token_hex(16)
            payload["effect_state"] = "applying"
            payload["effect_claimed_at"] = now.astimezone(UTC).isoformat()
            payload["effect_claim_token"] = claim_token
            current.payload = payload
            current.expires_at = now + self._ttl
            db.commit()
            action_id = current.id
        try:
            # The fence is server-owned and never part of the model/tool
            # contract.  Management uses it to make restore/re-save a hard
            # boundary against this claim's late effect.
            result = management.soft_delete(tenant, ids, effect_token=claim_token)
            rows = tuple(value.model_dump() if hasattr(value, "model_dump") else dict(value) for value in result.results)
        except Exception:
            self._record_delete_failure(
                action_id,
                claim_token,
                message_id=message_id,
                latest_turn_message_id=latest_turn_message_id,
            )
            return ConfirmationResult("effect_failed", item_ids=ids, action_id=action_id, error_code="delete_failed"), None
        with self._session_factory() as db:
            action = db.scalar(
                select(PendingChannelAction)
                .where(
                    PendingChannelAction.id == action_id,
                    PendingChannelAction.thread_id == thread_id,
                    PendingChannelAction.cancelled_at.is_(None),
                    PendingChannelAction.consumed_at.is_(None),
                )
                .with_for_update()
            )
            if action is None:
                return ConfirmationResult("effect_failed", item_ids=ids, error_code="delete_failed"), None
            payload = dict(action.payload)
            if (
                payload.get("effect_state") != "applying"
                or payload.get("effect_claim_token") != claim_token
            ):
                return ConfirmationResult(
                    "effect_failed", item_ids=ids, action_id=action_id,
                    error_code="delete_failed",
                ), None
            payload["effect_state"] = "applied"
            payload["effect_result"] = list(rows)
            payload.pop("effect_claim_token", None)
            payload.pop("effect_claimed_at", None)
            action.payload = payload
            now = db.scalar(select(func.now()))
            action.consumed_at = now
            action.consumed_message_id = message_id
            db.commit()
        return ConfirmationResult("confirmed", item_ids=ids, action_id=action_id, results=rows), result

    def _record_delete_failure(
        self,
        action_id: int,
        claim_token: str,
        *,
        message_id: str | None = None,
        latest_turn_message_id: str | None = None,
    ) -> None:
        try:
            with self._session_factory() as db:
                action = db.scalar(
                    select(PendingChannelAction)
                    .where(PendingChannelAction.id == action_id)
                    .with_for_update()
                )
                if action is None:
                    return
                payload = dict(action.payload) if isinstance(action.payload, dict) else {}
                if payload.get("effect_claim_token") != claim_token:
                    return
                now = self._as_utc(db.scalar(select(func.now())))
                payload["effect_state"] = "failed"
                payload["effect_error_code"] = "delete_failed"
                payload.pop("effect_result", None)
                payload.pop("effect_claim_token", None)
                payload.pop("effect_claimed_at", None)
                if message_id:
                    self._advance_confirmation_anchor(
                        payload,
                        message_id=message_id,
                        latest_turn_message_id=latest_turn_message_id,
                    )
                # A failed external attempt remains explicitly retryable;
                # do not let the original request TTL erase its trusted
                # target before the user can retry after a provider outage.
                action.expires_at = now + self._ttl
                action.payload = payload
                db.commit()
        except Exception:
            return

    def clarify_delete(
        self,
        tenant: TenantContext,
        thread_id: int,
        *,
        message_id: str | None = None,
        latest_turn_message_id: str | None = None,
    ) -> ConfirmationResult:
        # Tool-path clarifications advance the trusted confirmation chain in
        # the same transaction as the pending row read. Legacy/direct callers
        # without a raw message id retain the advisory read-only behavior.
        if not message_id:
            snapshot = self.inspect_delete(tenant, thread_id)
            if not snapshot.active:
                return ConfirmationResult("confirmation_missing")
            return ConfirmationResult("confirmation_required", item_ids=tuple(), action_id=None)
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            current = self._active(db, thread.id, kind="delete_saved_items")
            if current is None:
                return ConfirmationResult("confirmation_missing")
            now = self._as_utc(db.scalar(select(func.now())))
            payload = dict(current.payload) if isinstance(current.payload, dict) else {}
            effect_state = payload.get("effect_state", "pending")
            if effect_state == "applying":
                claim_at = self._payload_datetime(payload.get("effect_claimed_at"))
                if claim_at is None or claim_at + self._effect_claim_timeout > now:
                    return ConfirmationResult(
                        "effect_in_progress",
                        action_id=current.id,
                        error_code="delete_in_progress",
                    )
                # A stale applying lease can be clarified/re-anchored, but it
                # remains applying until a trusted confirmation reclaims it.
            elif self._as_utc(current.expires_at) <= now:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_expired")
            ids = self._payload_item_ids(current)
            if ids is None or not self._advance_confirmation_anchor(
                payload,
                message_id=message_id,
                latest_turn_message_id=latest_turn_message_id,
            ):
                return ConfirmationResult("confirmation_missing")
            current.payload = payload
            db.commit()
            return ConfirmationResult(
                "confirmation_required", item_ids=ids, action_id=current.id
            )

    def cancel_delete(self, tenant: TenantContext, thread_id: int) -> ConfirmationResult:
        with self._session_factory() as db:
            thread = self._lock_thread(db, tenant, thread_id)
            if thread is None:
                return ConfirmationResult("confirmation_missing")
            current = self._active(db, thread.id, kind="delete_saved_items")
            if current is None:
                return ConfirmationResult("confirmation_missing")
            now = self._as_utc(db.scalar(select(func.now())))
            if self._delete_effect_applying(current):
                # Cancellation cannot revoke an external effect after its
                # server-owned applying claim has been committed. Keep the
                # action recoverable instead of marking it cancelled while a
                # worker may still finalize it.
                return ConfirmationResult(
                    "effect_in_progress",
                    action_id=current.id,
                    error_code="delete_in_progress",
                )
            if self._as_utc(current.expires_at) <= now:
                current.cancelled_at = now
                db.commit()
                return ConfirmationResult("confirmation_expired")
            current.cancelled_at = now
            db.commit()
            return ConfirmationResult("cancelled", action_id=current.id)

    @staticmethod
    def _active(
        db: Session, thread_id: int, *, kind: str | None = None
    ) -> PendingChannelAction | None:
        stmt = select(PendingChannelAction)
        predicates = [
            PendingChannelAction.thread_id == thread_id,
            PendingChannelAction.consumed_at.is_(None),
            PendingChannelAction.cancelled_at.is_(None),
        ]
        if kind is not None:
            predicates.append(PendingChannelAction.kind == kind)
        stmt = stmt.where(*predicates)
        return db.scalar(
            stmt
            .order_by(PendingChannelAction.id.desc())
            .limit(1)
            .with_for_update()
        )

    @staticmethod
    def _active_any(db: Session, thread_id: int) -> PendingChannelAction | None:
        return PendingConfirmationService._active(db, thread_id)

    @staticmethod
    def _delete_effect_applying(action: PendingChannelAction | None) -> bool:
        """Whether a delete action has an external effect claim in flight."""

        if action is None or action.kind != "delete_saved_items":
            return False
        payload = action.payload
        return isinstance(payload, dict) and payload.get("effect_state") == "applying"

    @staticmethod
    def _canonical_urls(urls: list[str]) -> tuple[str, ...]:
        prepared = prepare_submission(urls)
        canonical: list[str] = []
        for item in prepared.items:
            if item.failure is not None:
                raise PendingValidationError(
                    item.failure.safe_error_code or item.failure.status
                )
            if item.reference is None:
                raise PendingValidationError("invalid_url")
            canonical.append(item.reference.canonical_url)
        return tuple(canonical)

    @staticmethod
    def _payload_urls(
        action: PendingChannelAction,
    ) -> tuple[str, ...] | None:
        payload = action.payload
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        urls = payload.get("urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            return None
        canonical_urls: list[str] = []
        for url in urls:
            if not isinstance(url, str):
                return None
            try:
                reference = normalize_item_reference(url)
            except ValueError:
                return None
            # Pending rows are written only by ``request_save`` after
            # canonicalization. Requiring byte-for-byte equality prevents a
            # manually corrupted, whitespace-padded, abbreviated, or merely
            # supported URL from becoming trusted model context.
            if url != reference.canonical_url:
                return None
            canonical_urls.append(reference.canonical_url)
        return tuple(canonical_urls)

    @staticmethod
    def _payload_item_ids(action: PendingChannelAction | None) -> tuple[int, ...] | None:
        if action is None or not isinstance(action.payload, dict):
            return None
        payload = action.payload
        if payload.get("version") != 1 or payload.get("kind") != "delete_saved_items":
            return None
        values = payload.get("item_ids")
        if not isinstance(values, list) or not 1 <= len(values) <= 10:
            return None
        ids: list[int] = []
        seen: set[int] = set()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value in seen:
                return None
            seen.add(value)
            ids.append(value)
        return tuple(ids)

    @staticmethod
    def _new_confirmation_code() -> str:
        # Avoid visually ambiguous characters in codes users may type.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(6))

    @staticmethod
    def _hash_confirmation_code(code: str) -> str:
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    @staticmethod
    def _extract_confirmation_code(message_text: str) -> str | None:
        if not isinstance(message_text, str):
            return None
        normalized = " ".join(message_text.upper().split())
        # Require the code to be in the current raw user message, adjacent to
        # an explicit delete-confirmation phrase.  History/model prose is not
        # consulted, so a delayed plain “yes” cannot consume a replacement.
        match = re.fullmatch(
            r"(?:确认删除|确认删掉|DELETE)\s+(?:验证码\s*)?([A-Z0-9]{6})[.!。！]?",
            normalized,
        )
        return match.group(1) if match else None

    @staticmethod
    def _is_plain_confirmation(message_text: str) -> bool:
        if not isinstance(message_text, str):
            return False
        normalized = " ".join(message_text.upper().split())
        return re.fullmatch(
            r"(?:YES|Y|确认|确认删除|是|好的|好|需要)[.!。！]?", normalized
        ) is not None

    @staticmethod
    def _payload_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(UTC)
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _request_is_current(
        payload: dict,
        *,
        latest_turn_message_id: str | None,
    ) -> bool:
        anchor = payload.get("confirmation_anchor_message_id")
        parent = payload.get("confirmation_anchor_parent_message_id")
        if not isinstance(anchor, str) or not anchor:
            # Legacy rows created before the confirmation chain was added use
            # their original request id as the initial anchor.
            anchor = payload.get("request_message_id")
        if not isinstance(latest_turn_message_id, str) or not latest_turn_message_id:
            return False
        return latest_turn_message_id in {
            value
            for value in (anchor, parent)
            if isinstance(value, str) and value
        }

    @classmethod
    def _advance_confirmation_anchor(
        cls,
        payload: dict,
        *,
        message_id: str,
        latest_turn_message_id: str | None,
    ) -> bool:
        """Advance a pending action only from its trusted prior anchor."""

        if not message_id.strip() or not cls._request_is_current(
            payload, latest_turn_message_id=latest_turn_message_id
        ):
            return False
        previous = payload.get("confirmation_anchor_message_id")
        if not isinstance(previous, str) or not previous:
            previous = payload.get("request_message_id")
        payload["confirmation_anchor_message_id"] = message_id
        payload["confirmation_anchor_parent_message_id"] = latest_turn_message_id
        if isinstance(previous, str) and previous:
            payload["confirmation_anchor_previous_message_id"] = previous
        return True

    @staticmethod
    def _payload_results(action: PendingChannelAction | None) -> tuple[dict, ...]:
        if action is None or not isinstance(action.payload, dict):
            return ()
        values = action.payload.get("effect_result")
        if not isinstance(values, list) or len(values) > 10:
            return ()
        return tuple(value for value in values if isinstance(value, dict))
