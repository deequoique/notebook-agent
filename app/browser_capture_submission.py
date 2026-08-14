"""Durable, tenant-scoped admission for extension-provided caption content."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.browser_capture import (
    BrowserCaptureRequest,
    MAX_CAPTURE_CUES,
    MAX_CAPTURE_TEXT_CHARS,
    canonical_transcript_bytes,
    canonicalize_reference,
    cue_content_hash,
    normalized_cues,
)
from app.channels.types import UserScope
from app.ingest.submission import IngestQuotaExceeded, IngestQuotaPolicy
from app.models import BrowserCapture, ContentItem, IngestDispatch
from app.object_store import ObjectStoreError


class BrowserCaptureSubmissionError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class BrowserCaptureResult:
    capture_public_id: str | None
    item_public_id: str
    platform: str
    status: str
    lifecycle: str
    safe_error_code: str | None = None


class BrowserCaptureSubmissionService:
    def __init__(
        self,
        session_factory,
        publisher,
        object_store,
        *,
        quota_policy: IngestQuotaPolicy,
        max_raw_bytes: int,
        max_cues: int = MAX_CAPTURE_CUES,
        max_text_chars: int = MAX_CAPTURE_TEXT_CHARS,
        trash_retention_days: int = 30,
    ) -> None:
        if (
            max_raw_bytes <= 0
            or max_cues <= 0
            or max_text_chars <= 0
            or trash_retention_days <= 0
        ):
            raise ValueError("capture limits must be positive")
        self._session_factory = session_factory
        self._publisher = publisher
        self._object_store = object_store
        self._quota_policy = quota_policy
        self._max_raw_bytes = max_raw_bytes
        self._max_cues = max_cues
        self._max_text_chars = max_text_chars
        self._trash_retention_days = trash_retention_days
        try:
            parameters = inspect.signature(publisher).parameters
            self._publisher_accepts_budget = (
                "remaining_budget_seconds" in parameters
                or any(
                    value.kind is inspect.Parameter.VAR_KEYWORD
                    for value in parameters.values()
                )
            )
        except (TypeError, ValueError):
            self._publisher_accepts_budget = False

    def submit(
        self,
        scope: UserScope,
        request: BrowserCaptureRequest,
        *,
        request_key: str,
        publish_budget_seconds: float | None = None,
    ) -> BrowserCaptureResult:
        if not request_key.strip():
            raise BrowserCaptureSubmissionError("capture_payload_invalid")
        if publish_budget_seconds is not None and publish_budget_seconds <= 0:
            raise ValueError("publish budget must be positive")
        canonical_url = canonicalize_reference(
            request.platform, request.platform_id, request.canonical_url
        )
        cues = normalized_cues(request.caption)
        normalized_hash = cue_content_hash(cues)
        if request.content_hash != normalized_hash:
            raise BrowserCaptureSubmissionError("capture_content_hash_mismatch")
        content_hash = normalized_hash if cues else None
        raw_body = canonical_transcript_bytes(request.caption) if cues else None
        if (
            (raw_body is not None and len(raw_body) > self._max_raw_bytes)
            or len(cues) > self._max_cues
            or sum(len(cue.text) for cue in cues) > self._max_text_chars
        ):
            raise BrowserCaptureSubmissionError("capture_too_large")
        body_hash = hashlib.sha256(
            request.model_dump_json(exclude_none=False).encode("utf-8")
        ).hexdigest()
        raw_key = (
            f"{scope.app_user_id}/{request.platform}/{request.platform_id}/"
            f"{content_hash}.capture.json"
            if content_hash is not None
            else None
        )
        deadline = (
            time.monotonic() + publish_budget_seconds
            if publish_budget_seconds is not None
            else None
        )

        try:
            admitted = self._admit(
                scope,
                request,
                request_key=request_key,
                body_hash=body_hash,
                canonical_url=canonical_url,
                raw_key=raw_key,
                content_hash=content_hash,
            )
        except IngestQuotaExceeded as exc:
            raise BrowserCaptureSubmissionError("quota_exceeded") from exc
        except IntegrityError:
            admitted = self._replay(scope, request_key, body_hash)
        if isinstance(admitted, BrowserCaptureResult):
            return admitted
        capture_id, capture_public_id, item_public_id, dispatch_id = admitted

        if raw_body is not None and raw_key is not None:
            try:
                self._object_store.put(raw_key, raw_body, "application/json")
            except Exception as exc:
                self._delete_raw_best_effort(raw_key)
                self._mark_failed(capture_id, dispatch_id, "capture_upload_failed")
                raise BrowserCaptureSubmissionError("capture_upload_failed") from (
                    exc if isinstance(exc, ObjectStoreError) else None
                )
        self._mark_capture_ready(capture_id)

        remaining = deadline - time.monotonic() if deadline is not None else None
        try:
            if remaining is not None and remaining <= 0:
                raise TimeoutError("broker_publish_timeout")
            if self._publisher_accepts_budget and remaining is not None:
                task_id = self._publisher(
                    dispatch_id, remaining_budget_seconds=remaining
                )
            else:
                task_id = self._publisher(dispatch_id)
        except Exception as exc:
            self._mark_failed(capture_id, dispatch_id, "queue_unavailable")
            raise BrowserCaptureSubmissionError("queue_unavailable") from None
        self._mark_enqueued(dispatch_id, task_id)
        return BrowserCaptureResult(
            capture_public_id,
            item_public_id,
            request.platform,
            "queued",
            "queued",
        )

    def _delete_raw_best_effort(self, raw_key: str) -> None:
        delete = getattr(self._object_store, "delete_object", None) or getattr(
            self._object_store, "delete", None
        )
        if delete is None:
            return
        try:
            delete(raw_key)
        except TypeError:
            try:
                delete(getattr(self._object_store, "bucket", None), raw_key)
            except Exception:
                return
        except Exception:
            return

    def _admit(
        self,
        scope: UserScope,
        request: BrowserCaptureRequest,
        *,
        request_key: str,
        body_hash: str,
        canonical_url: str,
        raw_key: str | None,
        content_hash: str | None,
    ) -> tuple[int, str, str, int] | BrowserCaptureResult:
        with self._session_factory() as db:
            if not self._quota_policy.acquire_locks(db, scope.app_user_id):
                raise BrowserCaptureSubmissionError("extension_account_disabled")
            replay = db.scalar(
                select(BrowserCapture).where(
                    BrowserCapture.app_user_id == scope.app_user_id,
                    BrowserCapture.request_key == request_key,
                )
            )
            if replay is not None:
                return self._result_for_capture(db, replay, body_hash)
            item = db.scalar(
                select(ContentItem)
                .where(
                    ContentItem.user_id == scope.app_user_id,
                    ContentItem.platform == request.platform,
                    ContentItem.platform_id == request.platform_id,
                )
                .with_for_update()
            )
            is_new = item is None
            if item is not None:
                restored = item.deleted_at is not None or item.archived_at is not None
                if item.deleted_at is not None:
                    now = db.scalar(select(func.now()))
                    if (
                        item.purge_claimed_at is not None
                        or item.deleted_at + timedelta(
                            days=self._trash_retention_days
                        )
                        <= now
                    ):
                        raise BrowserCaptureSubmissionError("capture_conflict")
                    item.deleted_at = None
                    item.delete_claim_token = uuid4().hex
                    item.purge_claimed_at = None
                    item.purge_attempts = 0
                    item.purge_error_code = None
                item.archived_at = None
                active = db.scalar(
                    select(IngestDispatch).where(
                        IngestDispatch.item_id == item.id,
                        IngestDispatch.state.in_(("pending", "enqueued", "running")),
                    )
                )
                if active is not None:
                    raise BrowserCaptureSubmissionError("capture_conflict")
                if item.state == "ready":
                    if restored:
                        db.commit()
                    return BrowserCaptureResult(
                        None,
                        item.public_id,
                        item.platform,
                        "already_exists",
                        "ready",
                    )
            self._quota_policy.enforce(
                db,
                scope.app_user_id,
                include_new_item_limits=is_new,
            )
            metadata = request.metadata
            if item is None:
                item = ContentItem(
                    public_id=uuid4().hex,
                    user_id=scope.app_user_id,
                    platform=request.platform,
                    platform_id=request.platform_id,
                    kind="video",
                    url=canonical_url,
                    state="pending",
                )
                db.add(item)
                db.flush()
                attempt = 1
            else:
                latest_attempt = db.scalar(
                    select(IngestDispatch.attempt)
                    .where(IngestDispatch.item_id == item.id)
                    .order_by(IngestDispatch.attempt.desc())
                    .limit(1)
                )
                attempt = int(latest_attempt or 0) + 1
                item.state = "pending"
                item.fail_reason = None
            item.url = canonical_url
            item.title = metadata.title
            item.author = metadata.author
            item.published_at = metadata.published_at
            item.duration_sec = metadata.duration_sec
            item.lang = metadata.language
            item.description = metadata.description
            item.tags = metadata.tags
            item.chapters = [chapter.model_dump() for chapter in metadata.chapters]
            item.cover_url = metadata.cover_url
            item.raw_object_key = raw_key
            item.raw_format = "capture_v1" if raw_key else "json3"
            dispatch = IngestDispatch(
                public_id=uuid4().hex,
                item_id=item.id,
                request_key=request_key,
                attempt=attempt,
                state="pending",
            )
            db.add(dispatch)
            db.flush()
            capture = BrowserCapture(
                public_id=uuid4().hex,
                app_user_id=scope.app_user_id,
                item_id=item.id,
                dispatch_id=dispatch.id,
                request_key=request_key,
                body_hash=body_hash,
                protocol_version=request.protocol_version,
                client_version=request.client_version,
                caption_status=request.caption.status,
                caption_source=request.caption.source,
                language=request.caption.language,
                capture_metadata=metadata.model_dump(mode="json"),
                raw_object_key=raw_key,
                content_hash=content_hash,
                state="staging",
            )
            db.add(capture)
            db.flush()
            result = (capture.id, capture.public_id, item.public_id, dispatch.id)
            db.commit()
            return result

    def _replay(
        self, scope: UserScope, request_key: str, body_hash: str
    ) -> BrowserCaptureResult:
        with self._session_factory() as db:
            capture = db.scalar(
                select(BrowserCapture).where(
                    BrowserCapture.app_user_id == scope.app_user_id,
                    BrowserCapture.request_key == request_key,
                )
            )
            if capture is None:
                raise BrowserCaptureSubmissionError("capture_conflict")
            return self._result_for_capture(db, capture, body_hash)

    @staticmethod
    def _result_for_capture(db, capture, body_hash: str) -> BrowserCaptureResult:
        if not hmac.compare_digest(str(capture.body_hash), str(body_hash)):
            raise BrowserCaptureSubmissionError("capture_conflict")
        item = db.get(ContentItem, capture.item_id)
        dispatch = db.get(IngestDispatch, capture.dispatch_id)
        if item is None or dispatch is None:
            raise BrowserCaptureSubmissionError("capture_conflict")
        if capture.state == "failed":
            raise BrowserCaptureSubmissionError(
                capture.error_code or "capture_upload_failed"
            )
        status = "queued" if dispatch.state in {"pending", "enqueued", "running"} else "completed"
        lifecycle = item.state if item.state in {"ready", "needs_asr"} else "queued"
        return BrowserCaptureResult(
            capture.public_id,
            item.public_id,
            item.platform,
            status,
            lifecycle,
        )

    def _mark_capture_ready(self, capture_id: int) -> None:
        with self._session_factory() as db:
            capture = db.get(BrowserCapture, capture_id)
            if capture is None or capture.state != "staging":
                return
            capture.state = "ready"
            capture.updated_at = datetime.now(UTC)
            db.commit()

    def _mark_enqueued(self, dispatch_id: int, task_id: str | None) -> None:
        with self._session_factory() as db:
            dispatch = db.get(IngestDispatch, dispatch_id)
            if dispatch is None or dispatch.state != "pending":
                return
            dispatch.state = "enqueued"
            dispatch.task_id = task_id
            dispatch.updated_at = datetime.now(UTC)
            db.commit()

    def _mark_failed(self, capture_id: int, dispatch_id: int, code: str) -> None:
        with self._session_factory() as db:
            capture = db.get(BrowserCapture, capture_id)
            dispatch = db.get(IngestDispatch, dispatch_id)
            if capture is not None:
                capture.state = "failed"
                capture.error_code = code
                capture.updated_at = datetime.now(UTC)
                item = db.get(ContentItem, capture.item_id)
                if item is not None:
                    item.state = "failed"
                    item.fail_reason = code
            if dispatch is not None and dispatch.state == "pending":
                dispatch.state = "failed"
                dispatch.error_code = code
                dispatch.updated_at = datetime.now(UTC)
            db.commit()
