"""Tenant-bound inventory and recycle-bin management services.

The model-facing agent never receives a session, tenant id, URL, storage key,
or dispatch id from this module.  ``KnowledgeItemManagementService`` is built
with a trusted :class:`TenantContext` and every method repeats the tenant
predicate in SQL.  The public projections intentionally contain only bounded
metadata suitable for a conversation response.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.config import Settings, get_settings
from app.models import ContentItem, IngestDispatch
from app.limits import normalize_why_saved


MAX_ITEM_LIMIT = 50
MAX_BATCH_SIZE = 10
CURSOR_VERSION = 1
_LOCATION = ("library", "trash")
_PURGE_LOGGER = logging.getLogger("notebook_agent.runtime")


class ManagementError(ValueError):
    """Stable, non-sensitive service error returned to the Agent."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class InvalidCursor(ManagementError):
    def __init__(self) -> None:
        super().__init__("invalid_cursor")


CursorError = InvalidCursor


class ItemNotFound(ManagementError):
    def __init__(self) -> None:
        # Missing and cross-tenant ids intentionally collapse to one code.
        super().__init__("item_not_found")


class InvalidBatch(ManagementError):
    def __init__(self) -> None:
        super().__init__("invalid_batch")


class InvalidWhySaved(ManagementError):
    def __init__(self) -> None:
        super().__init__("invalid_why_saved")


class SavedItem(BaseModel):
    """Bounded public inventory projection."""

    model_config = ConfigDict(extra="forbid")

    item_id: int
    platform: Literal["youtube", "bilibili", "wechat_mp", "ntu_kaltura"]
    kind: Literal["video", "article"]
    title: str
    author: str | None = None
    url: str
    duration_sec: int | None = None
    saved_at: datetime
    why_saved: str | None = None
    ingestion_state: str
    safe_error_code: str | None = None
    deleted_at: datetime | None = None
    expires_at: datetime | None = None
    restorable: bool | None = None


class SavedItemPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SavedItem] = Field(default_factory=list, max_length=MAX_ITEM_LIMIT)
    next_cursor: str | None = None


class ItemOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int
    status: str
    safe_error_code: str | None = None


class BatchItemOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ItemOperationResult] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)


@dataclass(frozen=True)
class ItemFilters:
    kind: str | None = None
    platform: str | None = None
    state: str | None = None
    location: Literal["library", "trash"] = "library"


def clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(MAX_ITEM_LIMIT, value))


def normalize_batch(item_ids: Iterable[int]) -> tuple[int, ...]:
    """Validate and de-duplicate a bounded id batch preserving first order."""

    try:
        values = list(item_ids)
    except TypeError:
        raise InvalidBatch() from None
    if not 1 <= len(values) <= MAX_BATCH_SIZE:
        raise InvalidBatch()
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            raise InvalidBatch()
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            raise InvalidBatch() from None
        if item_id <= 0:
            raise InvalidBatch()
        if item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    if not result:
        raise InvalidBatch()
    return tuple(result)


def _validate_item_id(item_id: int) -> int:
    if isinstance(item_id, bool):
        raise ItemNotFound()
    try:
        value = int(item_id)
    except (TypeError, ValueError):
        raise ItemNotFound() from None
    if value <= 0:
        raise ItemNotFound()
    return value


def _fingerprint(filters: ItemFilters) -> str:
    body = json.dumps(
        {
            "kind": filters.kind,
            "platform": filters.platform,
            "state": filters.state,
            "location": filters.location,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(body).hexdigest()[:24]


def encode_cursor(
    *,
    filters: ItemFilters,
    timestamp: datetime,
    item_id: int,
) -> str:
    """Create a versioned opaque keyset cursor (never a bearer credential)."""

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    payload = {
        "v": CURSOR_VERSION,
        "location": filters.location,
        "fingerprint": _fingerprint(filters),
        "order_timestamp": timestamp.astimezone(UTC).isoformat(),
        "id": int(item_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return encoded


def decode_cursor(cursor: str, *, filters: ItemFilters) -> tuple[datetime, int]:
    try:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("v") != CURSOR_VERSION:
            raise ValueError
        if payload.get("location") != filters.location:
            raise ValueError
        if payload.get("fingerprint") != _fingerprint(filters):
            raise ValueError
        item_id = payload.get("id")
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
            raise ValueError
        value = datetime.fromisoformat(str(payload.get("order_timestamp")))
        if value.tzinfo is None:
            raise ValueError
        return value.astimezone(UTC), item_id
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        raise InvalidCursor() from None


def _title(item: ContentItem) -> str:
    # The fallback is deliberately a platform id, never a raw URL or internal
    # object key.  Bound the value for conversation payloads.
    return (item.title or item.platform_id or "未命名条目")[:500]


def _safe_error_code(item: ContentItem) -> str | None:
    if item.state != "failed" and not item.purge_error_code:
        return None
    if item.purge_error_code:
        return "purge_failed"
    # fail_reason is provider/exception text and is never projected.
    return "ingestion_failed"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class KnowledgeItemManagementService:
    """Tenant-scoped inventory, update, soft-delete and restore operations."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        retention_days: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        if retention_days is None:
            try:
                retention_days = (settings or get_settings()).trash_retention_days
            except (RuntimeError, ValueError):
                # Pure service/unit callers may intentionally construct the
                # service before database environment variables are loaded.
                retention_days = 30
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self._session_factory = session_factory
        self.retention = timedelta(days=int(retention_days))

    def list_items(
        self,
        tenant: TenantContext,
        *,
        kind: str | None = None,
        platform: str | None = None,
        state: str | None = None,
        location: Literal["library", "trash"] = "library",
        limit: int = 20,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> SavedItemPage:
        filters = self._filters(kind, platform, state, location)
        limit = clamp_limit(limit)
        marker = decode_cursor(cursor, filters=filters) if cursor else None
        with self._session_factory() as db:
            now = _as_utc(now or db.scalar(select(func.now())) or datetime.now(UTC))
            stmt = select(ContentItem).where(ContentItem.user_id == tenant.app_user_id)
            if location == "library":
                stmt = stmt.where(
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                )
                if marker:
                    timestamp, item_id = marker
                    stmt = stmt.where(
                        or_(
                            ContentItem.saved_at < timestamp,
                            and_(ContentItem.saved_at == timestamp, ContentItem.id < item_id),
                        )
                    )
                stmt = stmt.order_by(ContentItem.saved_at.desc(), ContentItem.id.desc())
            else:
                stmt = stmt.where(ContentItem.deleted_at.is_not(None))
                if marker:
                    timestamp, item_id = marker
                    stmt = stmt.where(
                        or_(
                            ContentItem.deleted_at < timestamp,
                            and_(ContentItem.deleted_at == timestamp, ContentItem.id < item_id),
                        )
                    )
                stmt = stmt.order_by(ContentItem.deleted_at.desc(), ContentItem.id.desc())
            if kind is not None:
                stmt = stmt.where(ContentItem.kind == kind)
            if platform is not None:
                stmt = stmt.where(ContentItem.platform == platform)
            if state is not None:
                stmt = stmt.where(ContentItem.state == state)
            rows = list(db.scalars(stmt.limit(limit + 1)))
        has_next = len(rows) > limit
        rows = rows[:limit]
        projections = [self._project(row, location=location, now=now) for row in rows]
        next_cursor = None
        if has_next and rows:
            timestamp = rows[-1].saved_at if location == "library" else rows[-1].deleted_at
            if timestamp is not None:
                next_cursor = encode_cursor(
                    filters=filters, timestamp=timestamp, item_id=rows[-1].id
                )
        return SavedItemPage(items=projections, next_cursor=next_cursor)

    # Public names mirror the model-visible tools while keeping the internal
    # methods useful to callers that already use the shorter service names.
    def list_saved_items(self, tenant: TenantContext, **filters: Any) -> SavedItemPage:
        return self.list_items(tenant, **filters)

    def get_item(self, tenant: TenantContext, item_id: int) -> SavedItem:
        """Read one ordinary-library item; trash is always safe not-found."""

        item_id = _validate_item_id(item_id)
        with self._session_factory() as db:
            item = db.scalar(
                select(ContentItem).where(
                    ContentItem.id == item_id,
                    ContentItem.user_id == tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                )
            )
            if item is None:
                raise ItemNotFound()
            db_now = db.scalar(select(func.now())) or datetime.now(UTC)
            return self._project(item, location="library", now=_as_utc(db_now))

    # Explicitly named alias used by the model/runtime layer.
    get_saved_item = get_item

    def update_saved_item(
        self, tenant: TenantContext, item_id: int, why_saved: str | None
    ) -> ItemOperationResult:
        return self.update_why_saved(tenant, item_id, why_saved)

    def update_why_saved(
        self,
        tenant: TenantContext,
        item_id: int,
        why_saved: str | None,
    ) -> ItemOperationResult:
        item_id = _validate_item_id(item_id)
        normalized = self._normalize_why_saved(why_saved)
        with self._session_factory() as db:
            item = db.scalar(
                select(ContentItem)
                .where(
                    ContentItem.id == item_id,
                    ContentItem.user_id == tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                )
                .with_for_update()
            )
            if item is None:
                raise ItemNotFound()
            if item.why_saved == normalized:
                status = "unchanged"
            else:
                item.why_saved = normalized
                status = "updated"
            db.commit()
        return ItemOperationResult(item_id=item_id, status=status)

    def request_delete_targets(
        self, tenant: TenantContext, item_ids: Iterable[int]
    ) -> tuple[int, ...]:
        ids = normalize_batch(item_ids)
        with self._session_factory() as db:
            rows = list(
                db.scalars(
                    select(ContentItem)
                    .where(
                        ContentItem.id.in_(ids),
                        ContentItem.user_id == tenant.app_user_id,
                        ContentItem.deleted_at.is_(None),
                        ContentItem.archived_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(ids):
                raise ItemNotFound()
            # Validation is read-only from the service contract; the caller
            # persists only this verified id tuple in a pending action.
            db.rollback()
        return ids

    def soft_delete(
        self,
        tenant: TenantContext,
        item_ids: Iterable[int],
        *,
        now: datetime | None = None,
        effect_token: str | None = None,
    ) -> BatchItemOperationResult:
        ids = normalize_batch(item_ids)
        with self._session_factory() as db:
            now = _as_utc(now or db.scalar(select(func.now())) or datetime.now(UTC))
            rows = list(
                db.scalars(
                    select(ContentItem)
                    .where(ContentItem.id.in_(ids), ContentItem.user_id == tenant.app_user_id)
                    .with_for_update()
                )
            )
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(ids):
                raise ItemNotFound()
            results: list[ItemOperationResult] = []
            for item_id in ids:
                item = by_id[item_id]
                if item.deleted_at is not None:
                    if (
                        effect_token
                        and item.delete_claim_token
                        and item.delete_claim_token != effect_token
                    ):
                        # A different confirmed delete already owns this row;
                        # never let a late effect overwrite its fence.
                        results.append(
                            ItemOperationResult(
                                item_id=item_id, status="already_deleted"
                            )
                        )
                        continue
                    results.append(ItemOperationResult(item_id=item_id, status="already_deleted"))
                    continue
                if (
                    effect_token
                    and item.delete_claim_token
                    and item.delete_claim_token != effect_token
                ):
                    # The item was restored/re-saved after this effect was
                    # claimed.  The replacement fence makes this stale
                    # operation a no-op rather than a second deletion.
                    results.append(
                        ItemOperationResult(
                            item_id=item_id, status="already_restored"
                        )
                    )
                    continue
                item.deleted_at = now
                item.delete_claim_token = effect_token
                item.purge_claimed_at = None
                item.purge_error_code = None
                results.append(ItemOperationResult(item_id=item_id, status="deleted"))
            db.commit()
        return BatchItemOperationResult(results=results)

    def delete_saved_items(
        self, tenant: TenantContext, item_ids: Iterable[int], *, now: datetime | None = None
    ) -> BatchItemOperationResult:
        return self.soft_delete(tenant, item_ids, now=now)

    def restore(
        self,
        tenant: TenantContext,
        item_ids: Iterable[int],
        *,
        now: datetime | None = None,
    ) -> BatchItemOperationResult:
        ids = normalize_batch(item_ids)
        with self._session_factory() as db:
            now = _as_utc(now or db.scalar(select(func.now())) or datetime.now(UTC))
            rows = list(
                db.scalars(
                    select(ContentItem)
                    .where(ContentItem.id.in_(ids), ContentItem.user_id == tenant.app_user_id)
                    .with_for_update()
                )
            )
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(ids):
                raise ItemNotFound()
            results: list[ItemOperationResult] = []
            for item_id in ids:
                item = by_id[item_id]
                if item.deleted_at is None:
                    results.append(ItemOperationResult(item_id=item_id, status="already_restored"))
                    continue
                expiry = _as_utc(item.deleted_at) + self.retention
                if expiry <= now or item.purge_claimed_at is not None:
                    # Expired/claimed objects are intentionally indistinguish-
                    # able from a missing item to callers.
                    raise ItemNotFound()
                item.deleted_at = None
                item.archived_at = None
                item.delete_claim_token = uuid4().hex
                item.purge_claimed_at = None
                item.purge_attempts = 0
                item.purge_error_code = None
                results.append(ItemOperationResult(item_id=item_id, status="restored"))
            db.commit()
        return BatchItemOperationResult(results=results)

    def restore_saved_items(
        self, tenant: TenantContext, item_ids: Iterable[int], *, now: datetime | None = None
    ) -> BatchItemOperationResult:
        return self.restore(tenant, item_ids, now=now)

    @staticmethod
    def _normalize_why_saved(value: str | None) -> str | None:
        try:
            return normalize_why_saved(value)
        except ValueError:
            raise InvalidWhySaved()

    @staticmethod
    def _filters(
        kind: str | None,
        platform: str | None,
        state: str | None,
        location: str,
    ) -> ItemFilters:
        if location not in _LOCATION:
            raise ManagementError("invalid_location")
        if kind is not None and kind not in {"video", "article"}:
            raise ManagementError("invalid_filter")
        if platform is not None and platform not in {
            "youtube", "bilibili", "wechat_mp", "ntu_kaltura"
        }:
            raise ManagementError("invalid_filter")
        if state is not None and state not in {
            "pending", "fetching", "needs_extension", "needs_asr", "chunking",
            "embedding", "ready", "failed", "no_text",
        }:
            raise ManagementError("invalid_filter")
        return ItemFilters(kind, platform, state, location)  # type: ignore[arg-type]

    def _project(
        self, item: ContentItem, *, location: Literal["library", "trash"], now: datetime
    ) -> SavedItem:
        deleted = item.deleted_at
        expires = _as_utc(deleted) + self.retention if deleted is not None else None
        restorable = (
            location == "trash"
            and expires is not None
            and now < expires
            and item.purge_claimed_at is None
        )
        return SavedItem(
            item_id=item.id,
            platform=item.platform,
            kind=item.kind,
            title=_title(item),
            author=item.author,
            url=item.url,
            duration_sec=item.duration_sec,
            saved_at=item.saved_at,
            # Preserve previously stored notes exactly. The shared 500-character
            # contract applies to every new write, but silently truncating a
            # legacy row on read would misrepresent the user's saved data.
            why_saved=item.why_saved,
            ingestion_state=item.state,
            safe_error_code=_safe_error_code(item),
            deleted_at=deleted,
            expires_at=expires,
            restorable=restorable if location == "trash" else None,
        )


@dataclass(frozen=True)
class PurgeSweepResult:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    deferred: int = 0


class RecycleBinPurgeService:
    """Bounded, retryable two-phase MinIO + PostgreSQL recycle-bin purge."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        object_store: Any,
        *,
        retention_days: int = 30,
        batch_size: int = 20,
        claim_timeout_seconds: int = 1800,
        max_duration_seconds: float = 30.0,
    ) -> None:
        if (
            retention_days <= 0
            or batch_size <= 0
            or batch_size > 100
            or claim_timeout_seconds <= 0
            or max_duration_seconds <= 0
        ):
            raise ValueError("invalid purge settings")
        self._session_factory = session_factory
        self._object_store = object_store
        self.retention = timedelta(days=retention_days)
        self.batch_size = batch_size
        self.claim_timeout = timedelta(seconds=claim_timeout_seconds)
        self.max_duration_seconds = float(max_duration_seconds)

    def claim_expired(
        self,
        *,
        now: datetime | None = None,
        deadline: float | None = None,
    ) -> tuple[int, ...]:
        with self._session_factory() as db:
            remaining = self._remaining_budget(deadline)
            if deadline is not None and remaining <= 0:
                return ()
            self._set_statement_timeout(db, remaining)
            now = _as_utc(now or db.scalar(select(func.now())) or datetime.now(UTC))
            cutoff = now - self.retention
            stale = now - self.claim_timeout
            # A dispatch in pending/enqueued/running means a late worker may
            # still write an object.  Defer it rather than deleting the row.
            active = select(IngestDispatch.id).where(
                IngestDispatch.item_id == ContentItem.id,
                IngestDispatch.state.in_(("pending", "enqueued", "running")),
            )
            rows = list(
                db.scalars(
                    select(ContentItem)
                    .where(
                        ContentItem.deleted_at.is_not(None),
                        ContentItem.deleted_at <= cutoff,
                        or_(
                            ContentItem.purge_claimed_at.is_(None),
                            ContentItem.purge_claimed_at <= stale,
                        ),
                        ~active.exists(),
                    )
                    .order_by(ContentItem.deleted_at, ContentItem.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            ids: list[int] = []
            for item in rows:
                item.purge_claimed_at = now
                item.purge_attempts = int(item.purge_attempts or 0) + 1
                item.purge_error_code = None
                ids.append(item.id)
            db.commit()
            return tuple(ids)

    def purge_once(self, *, now: datetime | None = None) -> PurgeSweepResult:
        started = time.monotonic()
        deadline = started + self.max_duration_seconds
        timed_out = False
        timeout_phase: str | None = None

        def mark_timeout(phase: str) -> None:
            nonlocal timed_out, timeout_phase
            timed_out = True
            # Keep the first stage that exhausted the whole-sweep budget.  A
            # later cleanup/release query must not overwrite the useful
            # diagnostic with a less-specific phase.
            if timeout_phase is None:
                timeout_phase = phase

        if now is None:
            with self._session_factory() as db:
                remaining = self._remaining_budget(deadline)
                if remaining <= 0:
                    mark_timeout("prework")
                    now = datetime.now(UTC)
                else:
                    self._set_statement_timeout(db, remaining)
                    now = db.scalar(select(func.now())) or datetime.now(UTC)
                    if self._remaining_budget(deadline) <= 0:
                        mark_timeout("prework")
        now = _as_utc(now)
        if timed_out:
            deferred = 0
            ids: tuple[int, ...] = ()
        else:
            deferred = self._count_deferred(now, deadline=deadline)
            # ``_count_deferred`` is deliberately part of the bounded sweep:
            # if it consumed the remaining budget, do not start a claim
            # transaction merely to strand rows that cannot be processed.
            if self._remaining_budget(deadline) <= 0:
                mark_timeout("prework")
                ids = ()
            else:
                ids = self.claim_expired(now=now, deadline=deadline)
                # Claim itself is part of prework. If its transaction used up
                # the remaining sweep budget, record the timeout even when it
                # returned no rows; no later stage should be mistaken for the
                # source of the deadline breach.
                if self._remaining_budget(deadline) <= 0:
                    mark_timeout("prework")
        completed = failed = 0
        remaining: list[int] = []
        for index, item_id in enumerate(ids):
            if self._remaining_budget(deadline) <= 0:
                mark_timeout("processing")
                remaining.extend(ids[index:])
                break
            try:
                key: str | None
                with self._session_factory() as db:
                    row_budget = self._remaining_budget(deadline)
                    if row_budget <= 0:
                        mark_timeout("processing")
                        remaining.extend(ids[index:])
                        break
                    self._set_statement_timeout(db, row_budget)
                    item = db.get(ContentItem, item_id)
                    if item is None or item.deleted_at is None:
                        continue
                    key = item.raw_object_key
                if key:
                    object_budget = self._remaining_budget(deadline)
                    if object_budget <= 0:
                        mark_timeout("object_delete")
                        remaining.extend(ids[index:])
                        break
                    try:
                        self._delete_object(key, timeout_seconds=object_budget)
                    except Exception:
                        # Check the wall clock immediately after the provider
                        # returns an error, before best-effort row cleanup.
                        # This preserves object_delete as the timeout source
                        # even when this was the final claimed row.
                        failed += 1
                        if self._remaining_budget(deadline) <= 0:
                            mark_timeout("object_delete")
                        self._mark_failed(item_id, now, deadline=deadline)
                        continue
                with self._session_factory() as db:
                    finalize_budget = self._remaining_budget(deadline)
                    if finalize_budget <= 0:
                        mark_timeout("processing")
                        remaining.extend(ids[index:])
                        break
                    self._set_statement_timeout(db, finalize_budget)
                    item = db.scalar(
                        select(ContentItem)
                        .where(
                            ContentItem.id == item_id,
                            ContentItem.deleted_at.is_not(None),
                            ContentItem.purge_claimed_at == now,
                            ContentItem.deleted_at <= now - self.retention,
                        )
                        .with_for_update()
                    )
                    if item is not None:
                        db.delete(item)
                        db.commit()
                        completed += 1
            except Exception:
                failed += 1
                self._mark_failed(item_id, now, deadline=deadline)
        if remaining:
            self._release_claims(remaining, now, deadline=deadline)
            deferred += len(remaining)
        _PURGE_LOGGER.info(
            "diagnostic",
            extra={
                "diagnostic_payload": {
                    "event": "purge_sweep",
                    "stage": "purge_sweep",
                    "claimed": len(ids),
                    "completed": completed,
                    "failed": failed,
                    "deferred": deferred,
                    "timed_out": timed_out,
                    "timeout_phase": timeout_phase,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                }
            },
        )
        return PurgeSweepResult(
            claimed=len(ids), completed=completed, failed=failed, deferred=deferred
        )

    def _release_claims(
        self, item_ids: list[int], now: datetime, *, deadline: float | None = None
    ) -> None:
        try:
            with self._session_factory() as db:
                remaining = self._remaining_budget(deadline)
                # Cleanup is best-effort and must never turn an exhausted
                # sweep into an unbounded SQL transaction. The claim timeout
                # makes these rows eligible for a later retry.
                if deadline is not None and remaining <= 0:
                    return
                self._set_statement_timeout(db, remaining)
                rows = list(
                    db.scalars(
                        select(ContentItem)
                        .where(
                            ContentItem.id.in_(item_ids),
                            ContentItem.purge_claimed_at == now,
                        )
                        .with_for_update()
                    )
                )
                for item in rows:
                    item.purge_claimed_at = None
                db.commit()
        except Exception:
            return

    def _count_deferred(self, now: datetime, *, deadline: float | None = None) -> int:
        cutoff = now - self.retention
        try:
            with self._session_factory() as db:
                remaining = self._remaining_budget(deadline)
                if deadline is not None and remaining <= 0:
                    return 0
                self._set_statement_timeout(db, remaining)
                active = select(IngestDispatch.id).where(
                    IngestDispatch.item_id == ContentItem.id,
                    IngestDispatch.state.in_(("pending", "enqueued", "running")),
                )
                value = db.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.deleted_at.is_not(None),
                        ContentItem.deleted_at <= cutoff,
                        active.exists(),
                    )
                )
                return int(value or 0)
        except Exception:
            return 0

    def _delete_object(self, key: str, *, timeout_seconds: float | None = None) -> None:
        """Invoke one timeout-capable object-store adapter exactly once.

        ``TypeError`` from the provider is not a signature negotiation
        mechanism: once a call has started it may represent a provider bug or
        a transient failure, and retrying without the sweep timeout could
        block the maintenance worker indefinitely.  Resolve the supported
        argument shape with ``inspect.signature`` before making the call.
        Adapters that cannot retain ``timeout_seconds`` are rejected while
        the purge row is still claimed, so the caller can mark it retryable.
        """

        methods = [
            getattr(self._object_store, name, None)
            for name in ("delete_object", "delete")
        ]
        methods = [method for method in methods if callable(method)]
        if not methods:
            raise RuntimeError("object_delete_unavailable")

        try:
            bucket = getattr(self._object_store, "bucket", None)
        except Exception:
            bucket = None

        for delete in methods:
            try:
                signature = inspect.signature(delete)
            except (TypeError, ValueError):
                # A callable without an inspectable signature cannot prove it
                # preserves the timeout contract; fail safe before invoking.
                continue

            candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            if timeout_seconds is None:
                candidates.append(((key,), {}))
                # A required timeout parameter can still be satisfied by the
                # explicit ``None`` value when the adapter exposes it.
                candidates.append(((key,), {"timeout_seconds": None}))
                if bucket is not None:
                    candidates.append(((bucket, key), {}))
                    candidates.append(
                        ((bucket, key), {"timeout_seconds": None})
                    )
            else:
                # Only candidates carrying the remaining sweep budget are
                # eligible. There is deliberately no no-timeout fallback.
                candidates.append(
                    ((key,), {"timeout_seconds": timeout_seconds})
                )
                if bucket is not None:
                    candidates.append(
                        ((bucket, key), {"timeout_seconds": timeout_seconds})
                    )

            for args, kwargs in candidates:
                try:
                    signature.bind(*args, **kwargs)
                except TypeError:
                    continue
                # Do not catch TypeError here. It came from the adapter after
                # a validated call and must be handled as one purge failure.
                delete(*args, **kwargs)
                return

        if timeout_seconds is not None:
            raise RuntimeError("object_delete_timeout_unsupported")
        raise RuntimeError("object_delete_unsupported")

    def _mark_failed(
        self, item_id: int, now: datetime, *, deadline: float | None = None
    ) -> None:
        try:
            with self._session_factory() as db:
                remaining = self._remaining_budget(deadline)
                if deadline is not None and remaining <= 0:
                    return
                self._set_statement_timeout(db, remaining)
                item = db.scalar(
                    select(ContentItem)
                    .where(
                        ContentItem.id == item_id,
                        ContentItem.purge_claimed_at == now,
                    )
                    .with_for_update()
                )
                if item is not None:
                    item.purge_error_code = "object_delete_failed"
                    item.purge_claimed_at = None
                    db.commit()
        except Exception:
            return

    @staticmethod
    def _set_statement_timeout(db: Session, remaining_seconds: float) -> None:
        """Bound PostgreSQL stages without breaking the offline SQLite harness."""

        if remaining_seconds <= 0:
            return
        bind = getattr(db, "bind", None)
        dialect = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect == "postgresql":
            db.execute(
                text(
                    "SELECT set_config('statement_timeout', :timeout_text, true)"
                ),
                {
                    "timeout_text": f"{max(1, int(remaining_seconds * 1000))}ms"
                },
            )

    def _remaining_budget(self, deadline: float | None) -> float:
        if deadline is None:
            return self.max_duration_seconds
        return max(0.0, deadline - time.monotonic())


# Short alias for callers that do not need the recycle-bin wording.
PurgeService = RecycleBinPurgeService
