"""Tenant-bound asynchronous ingestion submission contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import inspect
import time
import re
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.channels.types import UserScope
from app.connectors.bilibili import canonicalize_bilibili_url
from app.connectors.youtube import YouTubeConnector
from app.config import get_settings
from app.models import (
    AppUser,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestDispatch,
)
from app.limits import normalize_why_saved


MAX_SAVE_BATCH_SIZE = 10
MIN_REMAINING_PUBLISH_BUDGET_SECONDS = 0.01


class BatchValidationError(ValueError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class EmptyBatch(BatchValidationError):
    def __init__(self) -> None:
        super().__init__("empty_batch")


class BatchTooLarge(BatchValidationError):
    def __init__(self) -> None:
        super().__init__("batch_too_large")


class InvalidURL(ValueError):
    pass


class UnsupportedURL(ValueError):
    pass


@dataclass(frozen=True)
class ItemReference:
    platform: str
    platform_id: str
    canonical_url: str


# Keep URL token handling in one place.  Action tools, the deterministic
# bare-URL route, and exact-reference retrieval all need to agree on where a
# URL ends (especially when a message ends in Chinese punctuation).
_MESSAGE_URL_RE = re.compile(
    r"https?://[^\s<>，。！？》」』】）\u3400-\u9fff]+",
    re.IGNORECASE,
)
_SAFE_URL_TRAILING_PUNCTUATION = ".,!?)]}，。！？》」"
_SAFE_MESSAGE_PUNCTUATION = (
    " \t\r\n.,!?)]}，。！？》」、:：;；()（）[]【】<>\"'“”‘’"
)


@dataclass(frozen=True)
class ParsedMessageReferences:
    """Server-owned interpretation of URLs in one current user message.

    ``ordered_urls`` retains every HTTP(S) token (including duplicates) for
    action input matching.  ``references`` is a stable, de-duplicated set of
    normalized platform references used to scope retrieval.  Unsupported or
    malformed URL tokens remain visible in ``unsupported_urls`` so they never
    accidentally become a successful bare supported-URL action.
    """

    ordered_urls: tuple[str, ...] = ()
    supported_urls: tuple[str, ...] = ()
    unsupported_urls: tuple[str, ...] = ()
    references: tuple[tuple[str, str], ...] = ()
    semantic_remainder: str = ""
    non_url_remainder: str = ""

    @property
    def has_supported_urls(self) -> bool:
        return bool(self.supported_urls)

    @property
    def has_semantic_text(self) -> bool:
        return bool(self.semantic_remainder)

    @property
    def is_bare_supported_url_batch(self) -> bool:
        """Whether the message has one unambiguous save-confirmation meaning."""

        return bool(self.supported_urls) and not self.unsupported_urls and not self.semantic_remainder

    @property
    def is_url_only_batch(self) -> bool:
        """Whether every semantic token in the message is an HTTP(S) URL."""

        return bool(self.ordered_urls) and not self.non_url_remainder


def parse_message_references(message: str) -> ParsedMessageReferences:
    """Extract and normalize supported URL tokens from the raw message.

    URL interpretation is deliberately deterministic and does not use model
    history.  Only the existing safe trailing punctuation is stripped from a
    token before passing it through :func:`normalize_item_reference`.
    """

    raw = str(message)
    ordered: list[str] = []
    supported: list[str] = []
    unsupported: list[str] = []
    references: list[tuple[str, str]] = []
    seen_references: set[tuple[str, str]] = set()
    remainder_parts: list[str] = []
    non_url_remainder_parts: list[str] = []
    cursor = 0

    for match in _MESSAGE_URL_RE.finditer(raw):
        token = match.group(0).rstrip(_SAFE_URL_TRAILING_PUNCTUATION)
        # A token containing only punctuation after the scheme is still an
        # invalid URL and must retain the safe validation path.
        if not token:
            token = match.group(0)
        ordered.append(token)
        non_url_remainder_parts.append(raw[cursor : match.start()])
        try:
            reference = normalize_item_reference(token)
        except (InvalidURL, UnsupportedURL, ValueError):
            unsupported.append(token)
            remainder_parts.append(raw[cursor : match.end()])
        else:
            supported.append(token)
            key = (reference.platform, reference.platform_id)
            if key not in seen_references:
                seen_references.add(key)
                references.append(key)
            # Remove the complete token and any punctuation captured directly
            # after it from semantic-text classification.
            remainder_parts.append(raw[cursor : match.start()])
        cursor = match.end()
    remainder_parts.append(raw[cursor:])
    non_url_remainder_parts.append(raw[cursor:])
    remainder = "".join(remainder_parts).translate(
        {ord(value): " " for value in _SAFE_MESSAGE_PUNCTUATION}
    )
    # A harmless punctuation-only remainder is not semantic text.  Preserve
    # meaningful words such as “保存” so an explicit save request cannot be
    # mistaken for a bare URL confirmation.
    semantic_remainder = " ".join(remainder.split())
    non_url_remainder = " ".join(
        "".join(non_url_remainder_parts)
        .translate({ord(value): " " for value in _SAFE_MESSAGE_PUNCTUATION})
        .split()
    )
    return ParsedMessageReferences(
        ordered_urls=tuple(ordered),
        supported_urls=tuple(supported),
        unsupported_urls=tuple(unsupported),
        references=tuple(references),
        semantic_remainder=semantic_remainder,
        non_url_remainder=non_url_remainder,
    )


@dataclass(frozen=True)
class SaveItemResult:
    result_id: str
    input_index: int
    status: Literal[
        "queued",
        "already_exists",
        "restored",
        "purge_in_progress",
        "retry_not_allowed",
        "unsupported_url",
        "invalid_url",
        "queue_unavailable",
        "create_failed",
        "quota_exceeded",
    ]
    item_id: int | None = None
    item_public_id: str | None = None
    state: str | None = None
    archived: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class BatchSaveResult:
    results: tuple[SaveItemResult, ...]


_INGEST_ADMISSION_LOCK_ID = 0x4E_4F_54_45_49_4E_47


class IngestQuotaExceeded(Exception):
    """Internal admission rejection with no private capacity details."""


@dataclass(frozen=True)
class IngestQuotaPolicy:
    """Atomic PostgreSQL admission policy shared by submit and retry paths."""

    max_active_per_tenant: int | None = None
    daily_new_item_limit: int | None = None
    max_items_per_tenant: int | None = None
    max_active_global: int | None = None
    daily_new_item_limit_global: int | None = None
    daily_dispatch_limit_per_tenant: int | None = None
    daily_dispatch_limit_global: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_active_per_tenant", self.max_active_per_tenant),
            ("daily_new_item_limit", self.daily_new_item_limit),
            ("max_items_per_tenant", self.max_items_per_tenant),
            ("max_active_global", self.max_active_global),
            (
                "daily_new_item_limit_global",
                self.daily_new_item_limit_global,
            ),
            (
                "daily_dispatch_limit_per_tenant",
                self.daily_dispatch_limit_per_tenant,
            ),
            (
                "daily_dispatch_limit_global",
                self.daily_dispatch_limit_global,
            ),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def enabled(self) -> bool:
        return any(
            value is not None
            for value in (
                self.max_active_per_tenant,
                self.daily_new_item_limit,
                self.max_items_per_tenant,
                self.max_active_global,
                self.daily_new_item_limit_global,
                self.daily_dispatch_limit_per_tenant,
                self.daily_dispatch_limit_global,
            )
        )

    def acquire_locks(self, db: Session, app_user_id: int) -> bool:
        """Acquire global then tenant locks in one stable deadlock-free order."""

        if not self.enabled:
            return True
        bind = db.get_bind()
        if self._global_limits_enabled and bind.dialect.name == "postgresql":
            db.execute(select(func.pg_advisory_xact_lock(_INGEST_ADMISSION_LOCK_ID)))
        user_id = db.scalar(
            select(AppUser.id)
            .where(AppUser.id == app_user_id)
            .with_for_update()
        )
        return user_id is not None

    def enforce(
        self,
        db: Session,
        app_user_id: int,
        *,
        include_new_item_limits: bool,
    ) -> None:
        if not self.enabled:
            return
        if self.max_active_global is not None:
            active_global = db.scalar(
                select(func.count(IngestDispatch.id)).where(
                    IngestDispatch.state.in_(("pending", "enqueued", "running"))
                )
            ) or 0
            if active_global >= self.max_active_global:
                raise IngestQuotaExceeded
        if self.max_active_per_tenant is not None:
            active_tenant = db.scalar(
                select(func.count(IngestDispatch.id))
                .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
                .where(
                    ContentItem.user_id == app_user_id,
                    IngestDispatch.state.in_(("pending", "enqueued", "running")),
                )
            ) or 0
            if active_tenant >= self.max_active_per_tenant:
                raise IngestQuotaExceeded
        if not include_new_item_limits:
            self._enforce_daily_dispatch_limits(db, app_user_id)
            return
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._enforce_daily_dispatch_limits(
            db,
            app_user_id,
            day_start=day_start,
        )
        if self.daily_new_item_limit_global is not None:
            daily_global = db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.saved_at >= day_start
                )
            ) or 0
            if daily_global >= self.daily_new_item_limit_global:
                raise IngestQuotaExceeded
        if self.daily_new_item_limit is not None:
            daily_tenant = db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.user_id == app_user_id,
                    ContentItem.saved_at >= day_start,
                )
            ) or 0
            if daily_tenant >= self.daily_new_item_limit:
                raise IngestQuotaExceeded
        if self.max_items_per_tenant is not None:
            total = db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.user_id == app_user_id
                )
            ) or 0
            if total >= self.max_items_per_tenant:
                raise IngestQuotaExceeded

    def _enforce_daily_dispatch_limits(
        self,
        db: Session,
        app_user_id: int,
        *,
        day_start: datetime | None = None,
    ) -> None:
        if (
            self.daily_dispatch_limit_per_tenant is None
            and self.daily_dispatch_limit_global is None
        ):
            return
        if day_start is None:
            now = datetime.now(UTC)
            day_start = now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        if self.daily_dispatch_limit_global is not None:
            daily_global = db.scalar(
                select(func.count(IngestDispatch.id)).where(
                    IngestDispatch.created_at >= day_start
                )
            ) or 0
            if daily_global >= self.daily_dispatch_limit_global:
                raise IngestQuotaExceeded
        if self.daily_dispatch_limit_per_tenant is not None:
            daily_tenant = db.scalar(
                select(func.count(IngestDispatch.id))
                .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
                .where(
                    ContentItem.user_id == app_user_id,
                    IngestDispatch.created_at >= day_start,
                )
            ) or 0
            if daily_tenant >= self.daily_dispatch_limit_per_tenant:
                raise IngestQuotaExceeded

    @property
    def _global_limits_enabled(self) -> bool:
        return (
            self.max_active_global is not None
            or self.daily_new_item_limit_global is not None
            or self.daily_dispatch_limit_global is not None
        )


@dataclass(frozen=True)
class PreparedItem:
    input_index: int
    reference: ItemReference | None = None
    failure: SaveItemResult | None = None


@dataclass(frozen=True)
class PreparedBatch:
    items: tuple[PreparedItem, ...]


def normalize_item_reference(url: str) -> ItemReference:
    """Normalize one supported item URL without remote connector calls."""

    value = str(url).strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InvalidURL("invalid URL")
    bilibili = canonicalize_bilibili_url(value)
    if bilibili is not None:
        platform_id, canonical_url = bilibili
        return ItemReference(
            platform="bilibili",
            platform_id=platform_id,
            canonical_url=canonical_url,
        )
    hostname = (parsed.hostname or "").lower()
    if hostname not in {
        "youtu.be",
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    }:
        raise UnsupportedURL("unsupported URL")
    connector = YouTubeConnector()
    platform_id = connector.match(value)
    if platform_id is None:
        raise UnsupportedURL("unsupported URL")
    return ItemReference(
        platform=connector.platform,
        platform_id=platform_id,
        canonical_url=f"https://www.youtube.com/watch?v={platform_id}",
    )


def prepare_submission(urls: list[str]) -> PreparedBatch:
    """Validate a bounded batch before any persistence or queue side effect."""

    values = list(urls)
    if not values:
        raise EmptyBatch()
    if len(values) > MAX_SAVE_BATCH_SIZE:
        raise BatchTooLarge()

    items: list[PreparedItem] = []
    for index, url in enumerate(values):
        try:
            reference = normalize_item_reference(url)
        except InvalidURL:
            items.append(
                PreparedItem(
                    input_index=index,
                    failure=SaveItemResult(
                        result_id=f"A{index + 1}",
                        input_index=index,
                        status="invalid_url",
                        safe_error_code="invalid_url",
                    ),
                )
            )
        except UnsupportedURL:
            items.append(
                PreparedItem(
                    input_index=index,
                    failure=SaveItemResult(
                        result_id=f"A{index + 1}",
                        input_index=index,
                        status="unsupported_url",
                        safe_error_code="unsupported_url",
                    ),
                )
            )
        else:
            items.append(PreparedItem(input_index=index, reference=reference))
    return PreparedBatch(tuple(items))


class IngestSubmissionService:
    """Create tenant-owned pending items and publish durable dispatch IDs."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: Callable[..., str | None],
        *,
        retention_days: int | None = None,
        max_active_per_tenant: int | None = None,
        daily_new_item_limit: int | None = None,
        max_items_per_tenant: int | None = None,
        max_active_global: int | None = None,
        daily_new_item_limit_global: int | None = None,
        daily_dispatch_limit_per_tenant: int | None = None,
        daily_dispatch_limit_global: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        if retention_days is None:
            try:
                retention_days = get_settings().trash_retention_days
            except (RuntimeError, ValueError):
                retention_days = 30
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self._retention = timedelta(days=int(retention_days))
        self._quota_policy = IngestQuotaPolicy(
            max_active_per_tenant=max_active_per_tenant,
            daily_new_item_limit=daily_new_item_limit,
            max_items_per_tenant=max_items_per_tenant,
            max_active_global=max_active_global,
            daily_new_item_limit_global=daily_new_item_limit_global,
            daily_dispatch_limit_per_tenant=daily_dispatch_limit_per_tenant,
            daily_dispatch_limit_global=daily_dispatch_limit_global,
        )
        try:
            signature = inspect.signature(publisher)
            self._publisher_accepts_budget = (
                "remaining_budget_seconds" in signature.parameters
                or any(
                    value.kind is inspect.Parameter.VAR_KEYWORD
                    for value in signature.parameters.values()
                )
            )
        except (TypeError, ValueError):
            self._publisher_accepts_budget = False

    def submit_urls(
        self,
        tenant: UserScope,
        urls: list[str],
        *,
        why_saved: str | None,
        request_key: str,
        publish_budget_seconds: float | None = None,
        source_thread_id: int | None = None,
    ) -> BatchSaveResult:
        if not request_key.strip():
            raise ValueError("request key is required")
        why_saved = normalize_why_saved(why_saved)
        prepared = prepare_submission(urls)
        deadline = (
            time.monotonic() + float(publish_budget_seconds)
            if publish_budget_seconds is not None
            else None
        )
        if publish_budget_seconds is not None and publish_budget_seconds <= 0:
            raise ValueError("publish budget must be positive")
        results: list[SaveItemResult] = []
        for item in prepared.items:
            if item.failure is not None:
                results.append(item.failure)
                continue
            results.append(
                self._submit_reference(
                    tenant,
                    item,
                    why_saved=why_saved,
                    request_key=request_key,
                    publish_deadline=deadline,
                    source_thread_id=source_thread_id,
                )
            )
        return BatchSaveResult(tuple(results))

    def _submit_reference(
        self,
        tenant: UserScope,
        prepared: PreparedItem,
        *,
        why_saved: str | None,
        request_key: str,
        publish_deadline: float | None,
        source_thread_id: int | None,
    ) -> SaveItemResult:
        reference = prepared.reference
        if reference is None:
            raise RuntimeError("prepared item has no reference")
        result_id = f"A{prepared.input_index + 1}"
        try:
            with self._session_factory() as db:
                if not self._quota_policy.acquire_locks(
                    db, tenant.app_user_id
                ):
                    raise RuntimeError("tenant does not exist")
                # Resolve the source target while the item/dispatch admission
                # transaction is open.  A failed or unsupported validation is
                # deliberately fail-closed to ``NULL``; it must never retarget
                # a notification to a later linked identity.
                validated_source_thread_id = self._validated_source_thread_id(
                    db, tenant, source_thread_id
                )
                existing = db.scalar(
                    select(ContentItem).where(
                        ContentItem.user_id == tenant.app_user_id,
                        ContentItem.platform == reference.platform,
                        ContentItem.platform_id == reference.platform_id,
                    ).with_for_update()
                )
                if existing is not None:
                    restored_from_trash = False
                    if getattr(existing, "deleted_at", None) is not None:
                        now = db.scalar(select(func.now()))
                        deleted_at = existing.deleted_at
                        if getattr(existing, "purge_claimed_at", None) is not None or (
                            deleted_at is not None
                            and deleted_at + self._retention <= now
                        ):
                            return SaveItemResult(
                                result_id=result_id,
                                input_index=prepared.input_index,
                                status="purge_in_progress",
                                item_id=existing.id,
                                state=existing.state,
                                safe_error_code="purge_in_progress",
                            )
                        existing.deleted_at = None
                        existing.delete_claim_token = uuid4().hex
                        existing.purge_claimed_at = None
                        existing.purge_attempts = 0
                        existing.purge_error_code = None
                        existing.archived_at = None
                        restored_from_trash = True
                        if why_saved is not None:
                            existing.why_saved = why_saved
                        # A restored ready/no-text/capability item is visible
                        # immediately and does not need a duplicate dispatch.
                        if existing.state not in {"failed", "pending"}:
                            db.commit()
                            return SaveItemResult(
                                result_id=result_id,
                                input_index=prepared.input_index,
                                status="restored",
                                item_id=existing.id,
                                item_public_id=existing.public_id,
                                state=existing.state,
                            )
                    if existing.archived_at is not None:
                        return self._already_exists(prepared, existing)
                    replay = db.scalar(
                        select(IngestDispatch).where(
                            IngestDispatch.item_id == existing.id,
                            IngestDispatch.request_key == request_key,
                        )
                    )
                    if replay is not None:
                        if restored_from_trash:
                            db.commit()
                        return self._existing_result(
                            prepared, existing, replay
                        )
                    if existing.state not in {"pending", "failed"}:
                        return self._already_exists(prepared, existing)
                    latest = db.scalar(
                        select(IngestDispatch)
                        .where(IngestDispatch.item_id == existing.id)
                        .order_by(IngestDispatch.attempt.desc())
                        .limit(1)
                    )
                    if latest is not None and latest.state in {
                        "pending",
                        "enqueued",
                        "running",
                        "completed",
                    }:
                        if restored_from_trash:
                            db.commit()
                        return self._already_exists(prepared, existing)
                    self._enforce_ingest_quota(
                        db,
                        tenant.app_user_id,
                        include_new_item_limits=False,
                    )
                    content = existing
                    content.state = "pending"
                    content.fail_reason = None
                    attempt = (latest.attempt + 1) if latest is not None else 1
                else:
                    self._enforce_ingest_quota(
                        db,
                        tenant.app_user_id,
                        include_new_item_limits=True,
                    )
                    content = ContentItem(
                        public_id=uuid4().hex,
                        user_id=tenant.app_user_id,
                        platform=reference.platform,
                        platform_id=reference.platform_id,
                        kind="video",
                        url=reference.canonical_url,
                        why_saved=why_saved,
                        state="pending",
                    )
                    db.add(content)
                    db.flush()
                    attempt = 1
                dispatch = IngestDispatch(
                    public_id=uuid4().hex,
                    item_id=content.id,
                    request_key=request_key,
                    attempt=attempt,
                    state="pending",
                    source_thread_id=validated_source_thread_id,
                )
                db.add(dispatch)
                db.flush()
                item_id = content.id
                item_public_id = content.public_id
                dispatch_id = dispatch.id
                db.commit()
        except IngestQuotaExceeded:
            return SaveItemResult(
                result_id=result_id,
                input_index=prepared.input_index,
                status="quota_exceeded",
                safe_error_code="quota_exceeded",
            )
        except IntegrityError:
            return self._result_after_conflict(
                tenant,
                prepared,
                reference,
                request_key=request_key,
            )
        except Exception:
            return SaveItemResult(
                result_id=result_id,
                input_index=prepared.input_index,
                status="create_failed",
                safe_error_code="create_failed",
            )

        remaining_budget = (
            publish_deadline - time.monotonic()
            if publish_deadline is not None
            else None
        )
        try:
            if (
                remaining_budget is not None
                and remaining_budget <= MIN_REMAINING_PUBLISH_BUDGET_SECONDS
            ):
                raise TimeoutError("broker_publish_timeout")
            if self._publisher_accepts_budget and remaining_budget is not None:
                task_id = self._publisher(
                    dispatch_id,
                    remaining_budget_seconds=remaining_budget,
                )
            else:
                task_id = self._publisher(dispatch_id)
        except Exception:
            self._set_dispatch_state(
                dispatch_id,
                state="failed",
                error_code="queue_unavailable",
            )
            return SaveItemResult(
                result_id=result_id,
                input_index=prepared.input_index,
                status="queue_unavailable",
                item_id=item_id,
                item_public_id=item_public_id,
                state="pending",
                safe_error_code="queue_unavailable",
            )

        self._set_dispatch_state(
            dispatch_id,
            state="enqueued",
            task_id=task_id,
        )
        return SaveItemResult(
            result_id=result_id,
            input_index=prepared.input_index,
            status="queued",
            item_id=item_id,
            item_public_id=item_public_id,
            state="pending",
        )

    def retry_item(
        self,
        tenant: UserScope,
        item_id: int,
        *,
        request_key: str,
        publish_budget_seconds: float | None = None,
        source_thread_id: int | None = None,
    ) -> SaveItemResult:
        """Queue one stable failed item for its next durable attempt.

        The request key is trusted application state (thread/action/message),
        never a model argument.  Replaying it returns the same dispatch
        result and the active-dispatch partial index prevents concurrent
        retries from creating two workers.
        """

        if not request_key.strip() or isinstance(item_id, bool):
            raise ValueError("retry requires item id and request key")
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            raise ValueError("retry requires item id and request key") from None
        if item_id <= 0:
            raise ValueError("retry requires item id and request key")
        if publish_budget_seconds is not None and publish_budget_seconds <= 0:
            raise ValueError("publish budget must be positive")
        publish_deadline = (
            time.monotonic() + float(publish_budget_seconds)
            if publish_budget_seconds is not None
            else None
        )
        result_id = "A1"
        item_public_id: str | None = None
        try:
            with self._session_factory() as db:
                if not self._quota_policy.acquire_locks(
                    db, tenant.app_user_id
                ):
                    return SaveItemResult(
                        result_id,
                        0,
                        "retry_not_allowed",
                        item_id=item_id,
                        safe_error_code="item_not_found",
                    )
                validated_source_thread_id = self._validated_source_thread_id(
                    db, tenant, source_thread_id
                )
                item = db.scalar(
                    select(ContentItem)
                    .where(ContentItem.id == item_id, ContentItem.user_id == tenant.app_user_id)
                    .with_for_update()
                )
                if (
                    item is None
                    or getattr(item, "deleted_at", None) is not None
                    or getattr(item, "archived_at", None) is not None
                ):
                    return SaveItemResult(result_id, 0, "retry_not_allowed", item_id=item_id, safe_error_code="item_not_found")
                item_public_id = item.public_id
                replay = db.scalar(
                    select(IngestDispatch).where(
                        IngestDispatch.item_id == item.id,
                        IngestDispatch.request_key == request_key,
                    )
                )
                if replay is not None:
                    repaired = self._repair_retry_split_state(db, item, replay)
                    if repaired:
                        db.commit()
                    return self._retry_result(item, replay)
                latest = db.scalar(
                    select(IngestDispatch)
                    .where(IngestDispatch.item_id == item.id)
                    .order_by(IngestDispatch.attempt.desc())
                    .limit(1)
                )
                # A prior retry may have committed the dispatch failure but
                # crashed before flipping the item back to ``failed``. Repair
                # that split state even when this request uses a fresh key;
                # otherwise a pending item would be permanently ineligible
                # for retry after a transient database outage.
                if (
                    item.state == "pending"
                    and latest is not None
                    and latest.state == "failed"
                    and latest.error_code == "queue_unavailable"
                ):
                    item.state = "failed"
                    item.fail_reason = "queue_unavailable"
                if item.state != "failed":
                    return SaveItemResult(result_id, 0, "retry_not_allowed", item_id=item.id, item_public_id=item.public_id, state=item.state, safe_error_code="retry_not_allowed")
                if latest is not None and latest.state in {"pending", "enqueued", "running"}:
                    return SaveItemResult(result_id, 0, "retry_not_allowed", item_id=item.id, item_public_id=item.public_id, state=item.state, safe_error_code="retry_not_allowed")
                self._enforce_ingest_quota(
                    db,
                    tenant.app_user_id,
                    include_new_item_limits=False,
                )
                item.state = "pending"
                item.fail_reason = None
                dispatch = IngestDispatch(
                    public_id=uuid4().hex,
                    item_id=item.id,
                    request_key=request_key,
                    attempt=(latest.attempt + 1) if latest is not None else 1,
                    state="pending",
                    source_thread_id=validated_source_thread_id,
                )
                db.add(dispatch)
                db.flush()
                dispatch_id = dispatch.id
                db.commit()
        except IngestQuotaExceeded:
            return SaveItemResult(
                result_id,
                0,
                "quota_exceeded",
                item_id=item_id,
                item_public_id=item_public_id,
                safe_error_code="quota_exceeded",
            )
        except IntegrityError:
            with self._session_factory() as db:
                item = db.get(ContentItem, item_id)
                dispatch = db.scalar(
                    select(IngestDispatch)
                    .where(IngestDispatch.item_id == item_id, IngestDispatch.request_key == request_key)
                )
                if item is not None and dispatch is not None:
                    return self._retry_result(item, dispatch)
            return SaveItemResult(result_id, 0, "retry_not_allowed", item_id=item_id, item_public_id=item_public_id, safe_error_code="retry_not_allowed")
        except Exception:
            return SaveItemResult(result_id, 0, "create_failed", item_id=item_id, item_public_id=item_public_id, safe_error_code="create_failed")

        remaining_budget = (
            publish_deadline - time.monotonic()
            if publish_deadline is not None
            else None
        )
        try:
            if (
                remaining_budget is not None
                and remaining_budget <= MIN_REMAINING_PUBLISH_BUDGET_SECONDS
            ):
                raise TimeoutError("broker_publish_timeout")
            if self._publisher_accepts_budget and remaining_budget is not None:
                task_id = self._publisher(
                    dispatch_id,
                    remaining_budget_seconds=remaining_budget,
                )
            else:
                task_id = self._publisher(dispatch_id)
        except Exception:
            if not self._mark_retry_publish_failed(dispatch_id):
                # The broker failed and the state transition could not be
                # durably recorded.  Do not claim a stable retry outcome;
                # admission will inspect/repair the row on the next attempt.
                return SaveItemResult(
                    result_id,
                    0,
                    "create_failed",
                    item_id=item_id,
                    item_public_id=item_public_id,
                    safe_error_code="create_failed",
                )
            return SaveItemResult(result_id, 0, "queue_unavailable", item_id=item_id, item_public_id=item_public_id, state="failed", safe_error_code="queue_unavailable")
        self._set_dispatch_state(dispatch_id, state="enqueued", task_id=task_id)
        return SaveItemResult(result_id, 0, "queued", item_id=item_id, item_public_id=item_public_id, state="pending")

    @staticmethod
    def _retry_result(item: ContentItem, dispatch: IngestDispatch) -> SaveItemResult:
        if dispatch.state in {"pending", "enqueued", "running"}:
            return SaveItemResult("A1", 0, "queued", item_id=item.id, item_public_id=item.public_id, state=item.state)
        if dispatch.state == "failed" and dispatch.error_code == "queue_unavailable":
            return SaveItemResult("A1", 0, "queue_unavailable", item_id=item.id, item_public_id=item.public_id, state=item.state, safe_error_code="queue_unavailable")
        return SaveItemResult("A1", 0, "retry_not_allowed", item_id=item.id, item_public_id=item.public_id, state=item.state, safe_error_code="retry_not_allowed")

    @staticmethod
    def _repair_retry_split_state(
        db: Session, item: ContentItem, dispatch: IngestDispatch
    ) -> bool:
        """Repair a crash between queue failure and the state transaction.

        A retry admission never treats ``pending item + failed dispatch`` as
        a stable success.  If an older process left that split state, repair
        both rows in the current transaction before reporting the durable
        queue failure.
        """

        if dispatch.state == "failed" and dispatch.error_code == "queue_unavailable" and item.state == "pending":
            item.state = "failed"
            item.fail_reason = "queue_unavailable"
            return True
        return False

    def _mark_retry_publish_failed(self, dispatch_id: int) -> bool:
        """Atomically make a failed retry dispatch and item retryable."""

        try:
            with self._session_factory() as db:
                dispatch = db.scalar(
                    select(IngestDispatch)
                    .where(IngestDispatch.id == dispatch_id)
                    .with_for_update()
                )
                if dispatch is None:
                    return False
                item = db.scalar(
                    select(ContentItem)
                    .where(ContentItem.id == dispatch.item_id)
                    .with_for_update()
                )
                if dispatch.state == "pending":
                    dispatch.state = "failed"
                    dispatch.error_code = "queue_unavailable"
                if item is not None and item.state == "pending":
                    item.state = "failed"
                    item.fail_reason = "queue_unavailable"
                db.commit()
                return True
        except Exception:
            return False

    retry_item_ingestion = retry_item

    @property
    def _quotas_enabled(self) -> bool:
        return self._quota_policy.enabled

    @property
    def quota_policy(self) -> IngestQuotaPolicy:
        return self._quota_policy

    def _enforce_ingest_quota(
        self,
        db: Session,
        app_user_id: int,
        *,
        include_new_item_limits: bool,
    ) -> None:
        self._quota_policy.enforce(
            db,
            app_user_id,
            include_new_item_limits=include_new_item_limits,
        )

    @staticmethod
    def _validated_source_thread_id(
        db: Session,
        tenant: TenantContext,
        source_thread_id: int | None,
    ) -> int | None:
        """Validate a server-owned source conversation in the admission tx.

        The caller supplies this value from ``AgentRequest.thread_db_id`` (or
        the trusted pending-action thread).  MCP/CLI and unsupported channels
        intentionally return ``None``.  A stale, cross-tenant, disabled, or
        routing-mismatched thread also returns ``None`` rather than allowing a
        guessed or newly linked target to receive a notification.
        """

        if source_thread_id is None:
            return None
        if isinstance(source_thread_id, bool):
            return None
        try:
            thread_id = int(source_thread_id)
        except (TypeError, ValueError):
            return None
        if thread_id <= 0 or tenant.channel not in {"telegram", "wechat"}:
            return None
        thread = db.scalar(
            select(ConversationThread)
            .where(ConversationThread.id == thread_id)
            .with_for_update()
        )
        if thread is None:
            return None
        if (
            thread.app_user_id != tenant.app_user_id
            or thread.channel_identity_id != tenant.channel_identity_id
            or thread.channel != tenant.channel
            or thread.channel not in {"telegram", "wechat"}
            or thread.account_id != tenant.account_id
        ):
            return None
        identity = db.get(ChannelIdentity, thread.channel_identity_id)
        if identity is None or identity.disabled_at is not None:
            return None
        if (
            identity.app_user_id != tenant.app_user_id
            or identity.channel != thread.channel
            or identity.account_id != thread.account_id
            or identity.external_user_id != tenant.external_user_id
        ):
            return None
        owner = db.get(AppUser, thread.app_user_id)
        if owner is None or owner.disabled_at is not None:
            return None
        return thread.id

    @staticmethod
    def _already_exists(
        prepared: PreparedItem, content: ContentItem
    ) -> SaveItemResult:
        return SaveItemResult(
            result_id=f"A{prepared.input_index + 1}",
            input_index=prepared.input_index,
            status="already_exists",
            item_id=content.id,
            item_public_id=content.public_id,
            state=content.state,
            archived=content.archived_at is not None,
        )
    def _existing_result(
        self,
        prepared: PreparedItem,
        content: ContentItem,
        dispatch: IngestDispatch,
    ) -> SaveItemResult:
        if dispatch.state == "pending":
            return self._already_exists(prepared, content)
        if (
            dispatch.state == "failed"
            and dispatch.error_code == "queue_unavailable"
        ):
            return SaveItemResult(
                result_id=f"A{prepared.input_index + 1}",
                input_index=prepared.input_index,
                status="queue_unavailable",
                item_id=content.id,
                item_public_id=content.public_id,
                state=content.state,
                safe_error_code="queue_unavailable",
            )
        return SaveItemResult(
            result_id=f"A{prepared.input_index + 1}",
            input_index=prepared.input_index,
            status="queued",
            item_id=content.id,
            item_public_id=content.public_id,
            state=content.state,
        )

    def _result_after_conflict(
        self,
        tenant: UserScope,
        prepared: PreparedItem,
        reference: ItemReference,
        *,
        request_key: str,
    ) -> SaveItemResult:
        try:
            with self._session_factory() as db:
                existing = db.scalar(
                    select(ContentItem).where(
                        ContentItem.user_id == tenant.app_user_id,
                        ContentItem.platform == reference.platform,
                        ContentItem.platform_id == reference.platform_id,
                    )
                )
                if existing is not None:
                    replay = db.scalar(
                        select(IngestDispatch).where(
                            IngestDispatch.item_id == existing.id,
                            IngestDispatch.request_key == request_key,
                        )
                    )
                    if replay is not None:
                        return self._existing_result(
                            prepared,
                            existing,
                            replay,
                        )
                    return self._already_exists(prepared, existing)
        except Exception:
            pass
        return SaveItemResult(
            result_id=f"A{prepared.input_index + 1}",
            input_index=prepared.input_index,
            status="create_failed",
            safe_error_code="create_failed",
        )

    def _set_dispatch_state(
        self,
        dispatch_id: int,
        *,
        state: str,
        task_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        try:
            with self._session_factory() as db:
                # Lock the row before checking pending. A worker may claim
                # and complete the dispatch between an unlocked read and the
                # publisher's state update; the lock makes this transaction
                # observe the worker's committed state instead of clobbering
                # it with enqueued/failed.
                dispatch = db.scalar(
                    select(IngestDispatch)
                    .where(IngestDispatch.id == dispatch_id)
                    .with_for_update()
                )
                if dispatch is None or dispatch.state != "pending":
                    return
                dispatch.state = state
                dispatch.task_id = task_id
                dispatch.error_code = error_code
                db.commit()
        except Exception:
            # Publishing may already have succeeded. A worker claims pending
            # dispatches safely, so never overwrite a faster worker transition
            # or expose persistence details to the Agent.
            return


def build_ingest_submission_service(
    session_factory: Callable[[], Session],
    publisher: Callable[..., str | None],
    settings,
) -> IngestSubmissionService:
    """Build the production submission service with one shared quota contract."""

    return IngestSubmissionService(
        session_factory,
        publisher,
        retention_days=settings.trash_retention_days,
        max_active_per_tenant=settings.ingest_max_active_per_user,
        daily_new_item_limit=settings.ingest_daily_new_item_limit,
        max_items_per_tenant=settings.ingest_max_items_per_user,
        max_active_global=settings.ingest_max_active_global,
        daily_new_item_limit_global=settings.ingest_daily_new_item_limit_global,
        daily_dispatch_limit_per_tenant=(
            settings.ingest_daily_dispatch_limit_per_user
        ),
        daily_dispatch_limit_global=settings.ingest_daily_dispatch_limit_global,
    )
