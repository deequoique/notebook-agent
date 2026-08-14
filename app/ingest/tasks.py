"""Synchronous ingestion core plus isolated Celery retry wrapper."""

from __future__ import annotations

import hashlib
import inspect as py_inspect
import json
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from celery import Celery, Task
from kombu import Producer, Queue
from sqlalchemy import and_, delete, func, inspect, or_, select, text

from app.config import Settings, get_settings
from app.browser_capture import parse_canonical_transcript
from app.connectors.base import NeedsASR, NeedsExtension, TextResult, TransientFetchError
from app.connectors.youtube import YouTubeConnector
from app.db import get_session_factory
from app.ingest.chunker import chunk
from app.ingest.embed import EmbeddingProvider, ZhipuEmbedder
from app.ingest.validate import IngestLimitExceeded, guard_ingest_limits, guard_transcript
from app.models import (
    AppUser,
    BrowserCapture,
    ContentItem,
    IngestCompletionEvent,
    IngestDispatch,
    Segment,
)
from app.limits import normalize_why_saved
from app.agent.management import RecycleBinPurgeService
from app.object_store import RawObjectStore
from app.ingest.notifications import IngestNotificationPoller
from app.tls import configure_trusted_ca


COMPLETION_QUEUE = "ingest-completion"
COMPLETION_TASK_NAME = "app.ingest.completion.consume"
_COMPLETION_QUEUES = (
    Queue("ingest", durable=True, auto_delete=False),
    Queue("maintenance", durable=True, auto_delete=False),
    Queue(COMPLETION_QUEUE, durable=True, auto_delete=False),
)

celery_app = Celery(
    "kb",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
celery_app.conf.task_queues = _COMPLETION_QUEUES
celery_app.conf.task_routes = {
    "app.ingest.tasks.fetch_text_task": {"queue": "ingest"},
    "app.ingest.tasks.purge_expired_items_task": {"queue": "maintenance"},
    "app.ingest.tasks.publish_pending_ingest_completion_events_task": {
        "queue": "maintenance"
    },
    "app.ingest.tasks.deliver_pending_ingest_notifications_task": {
        "queue": "maintenance"
    },
    COMPLETION_TASK_NAME: {"queue": COMPLETION_QUEUE},
}

_COMPLETION_LOGGER = logging.getLogger("notebook_agent.runtime")


def _completion_diagnostic(
    event: str,
    *,
    event_id: int | None = None,
    outcome: str | None = None,
    item_state: str | None = None,
    error_code: str | None = None,
    claimed: int | None = None,
    enqueued: int | None = None,
    failed: int | None = None,
    deferred: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Emit an allow-listed completion diagnostic without exception text."""

    payload: dict[str, Any] = {"event": event}
    if isinstance(event_id, int) and not isinstance(event_id, bool):
        payload["event_id"] = max(0, event_id)
    if outcome in {"completed", "failed"}:
        payload["outcome"] = outcome
    if item_state in {"ready", "needs_extension", "needs_asr", "failed"}:
        payload["item_state"] = item_state
    safe_error_codes = {
        "ingestion_failed",
        "transient_fetch_failed",
        "ingest_too_large",
        "item_deleted",
        "completion_publish_failed",
        "broker_unavailable",
    }
    if error_code in safe_error_codes:
        payload["error_code"] = error_code
    for key, value in {
        "claimed": claimed,
        "enqueued": enqueued,
        "failed": failed,
        "deferred": deferred,
        "duration_ms": duration_ms,
    }.items():
        if isinstance(value, int) and not isinstance(value, bool):
            payload[key] = max(0, value)
    try:
        _COMPLETION_LOGGER.info(
            "diagnostic", extra={"diagnostic_payload": payload}
        )
    except Exception:
        # Observability must never alter ingestion or maintenance semantics.
        return


def _bounded_publish_options(
    settings: Settings,
    *,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Build native Celery/Kombu publish bounds below the Agent deadline.

    Kombu applies ``max_retries`` both while publishing and while re-opening a
    lost connection.  We therefore reserve a small, fixed retry interval and
    divide the remaining budget across a conservative upper bound for those
    operations.  No thread or watchdog is needed (or left running) when the
    broker is unavailable.
    """

    budget = float(settings.broker_publish_timeout_seconds)
    agent_timeout = min(
        float(settings.agent_timeout_seconds),
        float(settings.agent_tool_timeout_seconds),
    )
    retries = int(settings.broker_publish_max_retries)
    if budget <= 0 or agent_timeout <= 0:
        raise ValueError("broker and Agent tool timeouts must be positive")
    if retries < 0:
        raise ValueError("BROKER_PUBLISH_MAX_RETRIES must be non-negative")

    # Reserve time for the surrounding Agent/channel request.  A
    # misconfigured larger value is clamped rather than allowing a broker call
    # to consume the whole model deadline, including for very small test
    # deadlines.
    agent_margin = min(1.0, agent_timeout / 2)
    budget = min(budget, agent_timeout - agent_margin)
    if budget_seconds is not None:
        budget = min(budget, float(budget_seconds))
    if budget <= 0:
        raise TimeoutError("broker_publish_timeout")
    attempts = retries + 1
    # One initial connection plus Kombu's bounded reconnect attempts for each
    # failed publish.  The multiplier leaves room for queue/exchange declare
    # and the Redis/AMQP socket operation on each attempt.
    operation_count = max(1, (attempts * (retries + 4)) // 2)
    sleep_count = retries * (retries + 3) // 2
    interval = min(0.1, budget / (4 * max(sleep_count, 1)))
    operation_budget = (budget - interval * sleep_count) / (4 * operation_count)

    return {
        "retry": True,
        "retry_policy": {
            "max_retries": retries,
            "interval_start": interval,
            "interval_step": 0,
            "interval_max": interval,
        },
        # Supported by Kombu Producer.publish (and by AMQP transports).
        "timeout": operation_budget,
        "_connect_timeout": operation_budget,
        "_socket_timeout": operation_budget,
        "_total_timeout": budget,
    }


def _connector(url: str) -> YouTubeConnector:
    settings = get_settings()
    # Resolve and export the verified CA before constructing the real
    # connector.  yt-dlp metadata and the isolated bounded subtitle child both
    # inherit this process environment; the later embedding composition keeps
    # its explicit SSLContext independently.
    configure_trusted_ca(settings.tls_ca_bundle)
    connector = YouTubeConnector(
        max_transcript_bytes=settings.ingest_max_raw_transcript_bytes,
        fetch_timeout_seconds=settings.youtube_fetch_timeout_seconds,
        proxy_url=settings.youtube_proxy_url,
    )
    if connector.match(url):
        return connector
    raise ValueError(f"unsupported URL: {url}")


def create_item(url: str, *, user_id: int, why_saved: str | None = None, connector: Any | None = None, session_factory=None) -> int:
    why_saved = normalize_why_saved(why_saved)
    connector = connector or _connector(url)
    platform_id = connector.match(url)
    if not platform_id:
        raise ValueError(f"connector does not match URL: {url}")
    factory = session_factory or get_session_factory()
    with factory() as db:
        if db.get(AppUser, user_id) is None:
            raise LookupError(f"app user {user_id} not found")
        existing = db.scalar(select(ContentItem).where(ContentItem.user_id == user_id, ContentItem.platform == connector.platform, ContentItem.platform_id == platform_id).with_for_update())
        if existing:
            if getattr(existing, "deleted_at", None) is not None:
                retention_days = get_settings().trash_retention_days
                now = db.scalar(select(func.now()))
                if getattr(existing, "purge_claimed_at", None) is not None or existing.deleted_at + timedelta(days=retention_days) <= now:
                    return existing.id
                existing.deleted_at = None
                existing.delete_claim_token = uuid4().hex
                existing.purge_claimed_at = None
                existing.purge_attempts = 0
                existing.purge_error_code = None
                existing.archived_at = None
                if why_saved is not None:
                    existing.why_saved = why_saved
                db.commit()
            return existing.id
        item = ContentItem(
            public_id=uuid4().hex,
            user_id=user_id,
            platform=connector.platform,
            platform_id=platform_id,
            kind="video",
            url=url,
            why_saved=why_saved,
            state="pending",
        )
        db.add(item)
        db.commit()
        return item.id


def process_item(item_id: int, *, connector: Any | None = None, embedder: EmbeddingProvider | None = None, object_store: Any | None = None, session_factory=None) -> str:
    factory = session_factory or get_session_factory()
    with factory() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            raise LookupError(f"content item {item_id} not found")
        if getattr(item, "deleted_at", None) is not None or getattr(item, "purge_claimed_at", None) is not None:
            _mark_item_deleted(db, item)
            return "deleted"

        settings = get_settings()
        store = object_store
        capture = None
        if connector is None:
            capture = db.scalar(
                select(BrowserCapture)
                .join(
                    IngestDispatch,
                    BrowserCapture.dispatch_id == IngestDispatch.id,
                )
                .where(
                    BrowserCapture.item_id == item.id,
                    BrowserCapture.state == "ready",
                    IngestDispatch.state == "running",
                )
                .order_by(BrowserCapture.created_at.desc())
                .limit(1)
            )
        pre_stored = capture is not None
        if capture is not None:
            item.state = "fetching"
            db.commit()
            if capture.caption_status == "unavailable":
                item.state = "needs_asr"
                item.text_source = "none"
                db.commit()
                return item.state
            if (
                not capture.raw_object_key
                or capture.caption_source not in {"official_cc", "auto_caption"}
                or not capture.language
            ):
                raise ValueError("captured_transcript_invalid")
            if store is None:
                store = RawObjectStore()
            body = store.get(
                capture.raw_object_key,
                max_bytes=settings.ingest_max_raw_transcript_bytes,
            )
            result = parse_canonical_transcript(
                body,
                source=capture.caption_source,
                language=capture.language,
            )
            key = capture.raw_object_key
        else:
            connector = connector or _connector(item.url)
            meta = connector.fetch_meta(item.platform_id)
            if meta is not None:
                item.url = meta.url
                item.title = meta.title
                item.author = meta.author
                item.published_at = meta.published_at
                item.duration_sec = meta.duration_sec
                item.lang = meta.lang
                item.description = meta.description
                item.tags = meta.tags
                item.chapters = meta.chapters
                item.cover_url = meta.cover_url
            item.state = "fetching"
            db.commit()
            result = connector.fetch_text(item.platform_id)

        if isinstance(result, NeedsExtension):
            item.state = "needs_extension"
            db.commit()
            return item.state
        if isinstance(result, NeedsASR):
            item.state = "needs_asr"
            db.commit()
            return item.state
        if not isinstance(result, TextResult):
            raise TypeError(f"connector returned unsupported text result: {type(result)!r}")
        guard_transcript(result.raw_body, result.cues, platform=item.platform)
        guard_ingest_limits(
            result.raw_body,
            result.cues,
            max_raw_bytes=settings.ingest_max_raw_transcript_bytes,
            max_cues=settings.ingest_max_cues_per_item,
            max_text_chars=settings.ingest_max_text_chars_per_item,
        )
        preflight_chunks = chunk(
            result.cues,
            lang=result.lang,
            chapters=item.chapters,
        )
        if len(preflight_chunks) > settings.ingest_max_segments_per_item:
            raise IngestLimitExceeded()
        if not pre_stored:
            key = f"{item.user_id}/{item.platform}/{item.platform_id}/{hashlib.sha256(result.raw_body).hexdigest()}.json3"
        db.refresh(item)
        if getattr(item, "deleted_at", None) is not None or getattr(item, "purge_claimed_at", None) is not None:
            _mark_item_deleted(db, item)
            return "deleted"
        item.raw_object_key = key
        item.raw_format = result.format
        item.content_hash = hashlib.sha256("\n".join(c.text.strip() for c in result.cues).encode()).hexdigest()
        item.text_source = result.source
        item.lang = result.lang
        item.state = "chunking"
        db.commit()
        if store is None:
            store = RawObjectStore()
        if not pre_stored:
            store.put(key, result.raw_body, "application/json")
        db.refresh(item)
        if getattr(item, "deleted_at", None) is not None or getattr(item, "purge_claimed_at", None) is not None:
            _delete_object_best_effort(store, key)
            _mark_item_deleted(db, item)
            return "deleted"
        if embedder is None:
            embedder = build_worker_embedder()
        remaining_embedding_chars = settings.ingest_max_embedding_chars_per_item

        def semantic(texts):
            nonlocal remaining_embedding_chars
            requested = sum(len(text) for text in texts)
            if requested > remaining_embedding_chars:
                raise IngestLimitExceeded()
            remaining_embedding_chars -= requested
            return embedder.embed(texts)

        chunks = chunk(result.cues, lang=result.lang, chapters=item.chapters, semantic_embedder=semantic)
        if len(chunks) > settings.ingest_max_segments_per_item:
            raise IngestLimitExceeded()
        vectors = semantic([part.text for part in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(
                f"embedding count mismatch: expected {len(chunks)}, got {len(vectors)}"
            )
        db.refresh(item)
        if getattr(item, "deleted_at", None) is not None or getattr(item, "purge_claimed_at", None) is not None:
            _delete_object_best_effort(store, key)
            _mark_item_deleted(db, item)
            return "deleted"
        db.execute(delete(Segment).where(Segment.item_id == item.id))
        item.state = "embedding"
        for seq, (part, vector) in enumerate(zip(chunks, vectors, strict=True)):
            fts = func.to_tsvector("english", part.text) if not result.lang.startswith("zh") else None
            db.add(Segment(item_id=item.id, seq=seq, start_sec=part.start_sec, end_sec=part.end_sec, text=part.text, embedding=vector, fts=fts, boundary_kind=part.boundary_kind))
        item.state = "ready"
        item.fail_reason = None
        db.commit()
        return item.state


def _delete_object_best_effort(store: Any, key: str) -> None:
    """Delete a late worker object without exposing key/provider details."""

    delete = getattr(store, "delete_object", None) or getattr(store, "delete", None)
    if delete is None:
        return
    try:
        delete(key)
    except TypeError:
        try:
            delete(getattr(store, "bucket", None), key)
        except Exception:
            return
    except Exception:
        return


def _mark_item_deleted(db: Any, item: Any) -> None:
    """Converge a worker abort into a durable retryable item state."""

    # A worker that returns ``deleted`` may have already persisted cleanup
    # intent/raw_object_key and then observed a soft-delete race.  Keep the
    # row visibly failed (rather than chunking/embedding forever) so restore
    # plus a later save/retry can create a fresh dispatch.
    item.state = "failed"
    item.fail_reason = "item_deleted"
    db.commit()


class IngestTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args:
            _mark_dispatch_failed(args[0], exc, task_id=task_id)


@celery_app.task(bind=True, base=IngestTask, autoretry_for=(TransientFetchError,), max_retries=5, retry_backoff=8, retry_backoff_max=600, retry_jitter=True)
def fetch_text_task(self, dispatch_id: int) -> str:
    return process_dispatch(
        dispatch_id,
        task_id=self.request.id,
    )


def publish_ingest_dispatch(
    dispatch_id: int,
    *,
    remaining_budget_seconds: float | None = None,
) -> str:
    """Publish only the durable internal dispatch identifier."""

    settings = get_settings()
    options = _bounded_publish_options(
        settings,
        budget_seconds=remaining_budget_seconds,
    )
    # These are read by Celery when it acquires the producer connection.  Keep
    # transport options scoped to the broker publish path; worker task retry /
    # backoff settings above remain unchanged.
    connect_timeout = options.pop("_connect_timeout")
    socket_timeout = options.pop("_socket_timeout")
    celery_app.conf.broker_connection_timeout = connect_timeout
    transport_options = dict(celery_app.conf.broker_transport_options or {})
    transport_options.update(
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
    )
    celery_app.conf.broker_transport_options = transport_options
    options.pop("_total_timeout")
    # Celery's shared ProducerPool has a bounded outer acquire but performs a
    # nested ConnectionPool.acquire(block=True) without forwarding that
    # timeout. Use a request-local bounded connection instead, so neither pool
    # can make an Agent tool thread wait indefinitely.
    with celery_app.connection_for_write(
        connect_timeout=connect_timeout,
        transport_options=transport_options,
    ) as connection:
        producer = Producer(connection)
        result = fetch_text_task.apply_async(
            args=[dispatch_id],
            producer=producer,
            **options,
        )
        return str(result.id)


@dataclass(frozen=True)
class CompletionSweepResult:
    """Safe counters returned by one bounded completion repair pass."""

    claimed: int = 0
    enqueued: int = 0
    failed: int = 0
    deferred: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "enqueued": self.enqueued,
            "failed": self.failed,
            "deferred": self.deferred,
            "duration_ms": self.duration_ms,
        }


class IngestCompletionPublisher:
    """Bounded, claim-based repair publisher for the completion outbox."""

    def __init__(
        self,
        session_factory,
        *,
        publisher: Callable[..., str | None] | None = None,
        batch_size: int = 20,
        claim_timeout_seconds: int = 300,
        max_duration_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if batch_size <= 0 or batch_size > 100:
            raise ValueError("completion batch_size must be between 1 and 100")
        if claim_timeout_seconds <= 0:
            raise ValueError("completion claim timeout must be positive")
        if max_duration_seconds <= 0:
            raise ValueError("completion max duration must be positive")
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = int(batch_size)
        self._claim_timeout_seconds = int(claim_timeout_seconds)
        self._max_duration_seconds = float(max_duration_seconds)
        self._clock = clock

    def _claim_batch(self, *, budget_seconds: float) -> list[tuple[int, str]]:
        """Claim rows in one short transaction, never across broker I/O."""

        with self._session_factory() as db:
            _set_completion_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            stale_before = now - timedelta(seconds=self._claim_timeout_seconds)
            statement = (
                select(IngestCompletionEvent)
                .where(
                    or_(
                        IngestCompletionEvent.publish_state == "pending",
                        and_(
                            IngestCompletionEvent.publish_state == "claimed",
                            or_(
                                IngestCompletionEvent.claimed_at.is_(None),
                                IngestCompletionEvent.claimed_at <= stale_before,
                            ),
                        ),
                    )
                )
                .order_by(IngestCompletionEvent.id)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            events = list(db.scalars(statement))
            claims: list[tuple[int, str]] = []
            for event in events:
                token = uuid4().hex
                event.publish_state = "claimed"
                event.claim_token = token
                event.claimed_at = now
                event.updated_at = now
                claims.append((event.id, token))
            if claims:
                db.commit()
            return claims

    def sweep_once(self) -> CompletionSweepResult:
        started = self._clock()
        try:
            claims = self._claim_batch(budget_seconds=self._max_duration_seconds)
        except Exception:
            # A database failure leaves all rows pending/claimed for a later
            # pass. Keep diagnostics numeric and do not leak driver details.
            duration = max(0, int((self._clock() - started) * 1000))
            _completion_diagnostic(
                "completion_event_sweep",
                failed=1,
                duration_ms=duration,
            )
            return CompletionSweepResult(failed=1, duration_ms=duration)

        claimed_count = len(claims)
        enqueued = 0
        failed = 0
        deferred = 0
        for index, (event_id, claim_token) in enumerate(claims):
            if self._clock() - started >= self._max_duration_seconds:
                deferred += len(claims) - index
                break
            try:
                publisher = self._publisher or publish_ingest_completion_event
                # The default publisher performs its own claim.  A sweep has
                # already claimed the row, so publish the envelope directly
                # and then conditionally ack with this sweep token.
                if self._publisher is None:
                    remaining = self._max_duration_seconds - (
                        self._clock() - started
                    )
                    if remaining <= 0:
                        deferred += len(claims) - index
                        break
                    task_id = self._publish_claimed(
                        event_id, budget_seconds=remaining
                    )
                else:
                    parameters = py_inspect.signature(publisher).parameters
                    if "session_factory" in parameters:
                        task_id = publisher(
                            event_id,
                            session_factory=self._session_factory,
                        )
                    else:
                        task_id = publisher(event_id)
                ack_budget = self._max_duration_seconds - (
                    self._clock() - started
                )
                if ack_budget <= 0:
                    # The broker may already have accepted this event. Leave
                    # the claim for timeout recovery rather than starting SQL
                    # beyond the whole-sweep deadline; a duplicate is valid.
                    deferred += 1
                    continue
                if _mark_completion_enqueued(
                    event_id,
                    claim_token,
                    task_id,
                    session_factory=self._session_factory,
                    budget_seconds=ack_budget,
                ):
                    enqueued += 1
                else:
                    # Test/injected publishers may update the row themselves;
                    # the claim-token guard deliberately classifies that race
                    # as deferred rather than acknowledging another publisher.
                    deferred += 1
            except Exception:
                failed += 1
                try:
                    release_budget = self._max_duration_seconds - (
                        self._clock() - started
                    )
                    if release_budget <= 0:
                        continue
                    _release_completion_claim(
                        event_id,
                        claim_token,
                        session_factory=self._session_factory,
                        budget_seconds=release_budget,
                    )
                except Exception:
                    # Leave an un-released claim for the timeout recovery
                    # branch if the release transaction itself is unavailable.
                    pass

        duration = max(0, int((self._clock() - started) * 1000))
        _completion_diagnostic(
            "completion_event_sweep",
            claimed=claimed_count,
            enqueued=enqueued,
            failed=failed,
            deferred=deferred,
            duration_ms=duration,
        )
        return CompletionSweepResult(
            claimed=claimed_count,
            enqueued=enqueued,
            failed=failed,
            deferred=deferred,
            duration_ms=duration,
        )

    # Alias used by maintenance callers and older operational scripts.
    publish_pending = sweep_once

    def _publish_claimed(
        self, event_id: int, *, budget_seconds: float | None = None
    ) -> str | None:
        """Publish a row already claimed by this sweep."""

        settings = get_settings()
        options = _bounded_publish_options(
            settings, budget_seconds=budget_seconds
        )
        connect_timeout = options.pop("_connect_timeout")
        socket_timeout = options.pop("_socket_timeout")
        options.pop("_total_timeout")
        celery_app.conf.broker_connection_timeout = connect_timeout
        transport_options = dict(celery_app.conf.broker_transport_options or {})
        transport_options.update(
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
        )
        celery_app.conf.broker_transport_options = transport_options
        with celery_app.connection_for_write(
            connect_timeout=connect_timeout,
            transport_options=transport_options,
        ) as connection:
            producer = Producer(connection)
            result = celery_app.send_task(
                COMPLETION_TASK_NAME,
                args=[event_id],
                queue=COMPLETION_QUEUE,
                producer=producer,
                declare=[_COMPLETION_QUEUES[-1]],
                delivery_mode=2,
                **options,
            )
        return str(getattr(result, "id", "") or "") or None


@celery_app.task(name="app.ingest.tasks.publish_pending_ingest_completion_events_task")
def publish_pending_ingest_completion_events_task() -> dict[str, int]:
    """Repair pending/stale completion rows on the maintenance queue."""

    settings = get_settings()
    service = IngestCompletionPublisher(
        get_session_factory(),
        batch_size=settings.ingest_completion_batch_size,
        claim_timeout_seconds=settings.ingest_completion_claim_timeout_seconds,
        max_duration_seconds=settings.ingest_completion_max_duration_seconds,
    )
    return service.sweep_once().as_dict()


@celery_app.task(name="app.ingest.tasks.deliver_pending_ingest_notifications_task")
def deliver_pending_ingest_notifications_task() -> dict[str, int]:
    """Deliver source-channel completion notifications on maintenance.

    The task has no event/target arguments.  PostgreSQL is the durable source
    and the delivery ledger owns claims; Celery retries are intentionally not
    used so one slow or failed outbound event cannot replay an entire batch.
    """

    settings = get_settings()
    result = IngestNotificationPoller(
        get_session_factory(), settings=settings
    ).sweep_once()
    return result.as_dict()


@celery_app.task(name="app.ingest.tasks.purge_expired_items_task")
def purge_expired_items_task() -> dict[str, int]:
    """Run one bounded recycle-bin sweep and emit only safe counters."""

    settings = get_settings()
    service = RecycleBinPurgeService(
        get_session_factory(),
        RawObjectStore(),
        retention_days=settings.trash_retention_days,
        batch_size=settings.trash_purge_batch_size,
        claim_timeout_seconds=settings.trash_purge_claim_timeout_seconds,
        max_duration_seconds=settings.trash_purge_max_duration_seconds,
    )
    result = service.purge_once()
    return {
        "claimed": result.claimed,
        "completed": result.completed,
        "failed": result.failed,
        "deferred": result.deferred,
    }


try:
    _purge_interval = max(1, int(os.getenv("TRASH_PURGE_INTERVAL_SECONDS", "3600")))
except (TypeError, ValueError):
    _purge_interval = 3600

def _completion_interval_from_env() -> int:
    """Fail closed when beat's completion schedule is misconfigured."""

    raw_value = os.getenv(
        "INGEST_COMPLETION_INTERVAL_SECONDS",
        os.getenv("INGEST_COMPLETION_PUBLISH_INTERVAL_SECONDS", "60"),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "INGEST_COMPLETION_INTERVAL_SECONDS must be a positive integer"
        ) from exc
    if value <= 0:
        raise ValueError(
            "INGEST_COMPLETION_INTERVAL_SECONDS must be a positive integer"
        )
    return value


def _notification_interval_from_env() -> int:
    """Fail closed for the bounded source-channel notification schedule."""

    raw_value = os.getenv("INGEST_NOTIFICATION_INTERVAL_SECONDS", "10")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "INGEST_NOTIFICATION_INTERVAL_SECONDS must be a positive integer"
        ) from exc
    if value <= 0:
        raise ValueError(
            "INGEST_NOTIFICATION_INTERVAL_SECONDS must be a positive integer"
        )
    return value


_notification_interval = _notification_interval_from_env()

celery_app.conf.beat_schedule = {
    "deliver-pending-ingest-notifications": {
        "task": "app.ingest.tasks.deliver_pending_ingest_notifications_task",
        "schedule": float(_notification_interval),
        "options": {"queue": "maintenance"},
    },
    "purge-expired-items": {
        "task": "app.ingest.tasks.purge_expired_items_task",
        "schedule": float(_purge_interval),
        "options": {"queue": "maintenance"},
    }
}


def build_worker_embedder(
    settings: Settings | None = None,
) -> EmbeddingProvider:
    """Build worker HTTPS embedding with the verified shared CA contract."""

    settings = settings or get_settings()
    trusted_ca = configure_trusted_ca(settings.tls_ca_bundle)
    return ZhipuEmbedder(
        settings.zhipu_api_key or "",
        model=settings.embedding_model,
        endpoint=settings.embedding_endpoint,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        ssl_context=trusted_ca.ssl_context,
    )


def process_dispatch(
    dispatch_id: int,
    *,
    task_id: str | None,
    processor: Callable[[int], str] | None = None,
    session_factory=None,
) -> str:
    """Claim one dispatch; duplicate deliveries never rerun ingestion."""

    factory = session_factory or get_session_factory()
    item_id = _claim_dispatch(
        dispatch_id, task_id, session_factory=factory
    )
    if item_id is None:
        return "duplicate"
    try:
        state = (processor or process_item)(item_id)
    except TransientFetchError:
        _release_dispatch_for_retry(
            dispatch_id, task_id, session_factory=factory
        )
        # Celery may log task exceptions; preserve retry type but never copy
        # connector/provider details into the task failure surface.
        raise TransientFetchError("transient_fetch_failed") from None
    except IngestLimitExceeded as exc:
        _mark_dispatch_failed(
            dispatch_id, exc, task_id=task_id, session_factory=factory
        )
        raise RuntimeError("ingest_too_large") from None
    except Exception as exc:
        _mark_dispatch_failed(
            dispatch_id, exc, task_id=task_id, session_factory=factory
        )
        raise RuntimeError("ingestion_failed") from None
    _complete_dispatch(
        dispatch_id, task_id, process_state=state, session_factory=factory
    )
    return state


def _claim_dispatch(
    dispatch_id: int,
    task_id: str | None,
    *,
    session_factory=None,
) -> int | None:
    factory = session_factory or get_session_factory()
    deleted_event_id: int | None = None
    deleted_event_created = False
    with factory() as db:
        dispatch = db.scalar(
            select(IngestDispatch)
            .where(IngestDispatch.id == dispatch_id)
            .with_for_update()
        )
        if dispatch is None or dispatch.state not in {
            "pending",
            "enqueued",
        }:
            return None
        if (
            dispatch.task_id is not None
            and task_id is not None
            and dispatch.task_id != task_id
        ):
            return None
        item = db.get(ContentItem, dispatch.item_id)
        if item is None:
            # A tenant merge may retire a queued duplicate and cascade its
            # dispatch before this delivery is claimed. Treat that as the same
            # no-op duplicate outcome as a missing dispatch.
            return None
        if getattr(item, "deleted_at", None) is not None or getattr(item, "purge_claimed_at", None) is not None:
            dispatch.state = "failed"
            dispatch.error_code = "item_deleted"
            item.state = "failed"
            item.fail_reason = "item_deleted"
            dispatch.updated_at = datetime.now(UTC)
            deleted_event_id, deleted_event_created = _ensure_completion_event(
                db,
                dispatch,
                item,
                outcome="failed",
                item_state="failed",
                error_code="item_deleted",
            )
            db.commit()
        else:
            dispatch.state = "running"
            if task_id is not None:
                dispatch.task_id = task_id
            dispatch.updated_at = datetime.now(UTC)
            db.commit()
            return item.id
    if deleted_event_id is not None:
        if deleted_event_created:
            _completion_diagnostic(
                "completion_event_created",
                event_id=deleted_event_id,
                outcome="failed",
                item_state="failed",
                error_code="item_deleted",
            )
        # Source-channel notifications are owned by the periodic PostgreSQL
        # poller.  Keep the durable event row; do not publish the retired
        # completion Redis envelope from a request/worker terminal hook.
    return None


def _release_dispatch_for_retry(
    dispatch_id: int,
    task_id: str | None,
    *,
    session_factory=None,
) -> None:
    factory = session_factory or get_session_factory()
    with factory() as db:
        dispatch = db.scalar(
            select(IngestDispatch)
            .where(IngestDispatch.id == dispatch_id)
            .with_for_update()
        )
        if (
            dispatch is None
            or dispatch.state != "running"
            or (
                task_id is not None
                and dispatch.task_id not in {None, task_id}
            )
        ):
            return
        dispatch.state = "enqueued"
        dispatch.updated_at = datetime.now(UTC)
        db.commit()


def _complete_dispatch(
    dispatch_id: int,
    task_id: str | None,
    *,
    process_state: str | None = None,
    session_factory=None,
) -> int | None:
    factory = session_factory or get_session_factory()
    event_id: int | None = None
    event_created = False
    event_outcome: str | None = None
    event_item_state: str | None = None
    with factory() as db:
        dispatch = db.scalar(
            select(IngestDispatch)
            .where(IngestDispatch.id == dispatch_id)
            .with_for_update()
        )
        if (
            dispatch is None
            or dispatch.state != "running"
            or (
                task_id is not None
                and dispatch.task_id not in {None, task_id}
            )
        ):
            return None
        item = db.get(ContentItem, dispatch.item_id)
        if (
            item is None
            or process_state == "deleted"
            or getattr(item, "deleted_at", None) is not None
            or getattr(item, "purge_claimed_at", None) is not None
        ):
            dispatch.state = "failed"
            dispatch.error_code = "item_deleted"
            if item is not None:
                item.state = "failed"
                item.fail_reason = "item_deleted"
            dispatch.updated_at = datetime.now(UTC)
            if item is not None:
                event_item_state = "failed"
                event_outcome = "failed"
                event_id, event_created = _ensure_completion_event(
                    db,
                    dispatch,
                    item,
                    outcome="failed",
                    item_state=event_item_state,
                    error_code="item_deleted",
                )
            db.commit()
        else:
            event_item_state = _terminal_item_state(item, process_state)
            event_outcome = "completed"
            dispatch.state = "completed"
            dispatch.error_code = None
            dispatch.updated_at = datetime.now(UTC)
            event_id, event_created = _ensure_completion_event(
                db,
                dispatch,
                item,
                outcome=event_outcome,
                item_state=event_item_state,
                error_code=None,
            )
            db.commit()
    if event_id is not None:
        if event_created:
            _completion_diagnostic(
                "completion_event_created",
                event_id=event_id,
                outcome=event_outcome,
                item_state=event_item_state,
                error_code="item_deleted" if event_outcome == "failed" else None,
            )
        # The event is durable and independently discoverable by the
        # notification ledger poller; no immediate broker publication here.
    return event_id


def _terminal_item_state(item: Any, process_state: str | None) -> str:
    """Project the worker result to the small completion-event vocabulary."""

    state = getattr(item, "state", None)
    if state not in {"ready", "needs_extension", "needs_asr"}:
        raise ValueError("completion_item_state_not_terminal")
    if process_state is not None and process_state != state:
        raise ValueError("completion_process_state_mismatch")
    return state


def _ensure_completion_event(
    db: Any,
    dispatch: Any,
    item: Any,
    *,
    outcome: str,
    item_state: str,
    error_code: str | None,
) -> tuple[int | None, bool]:
    """Insert-or-read the one outbox row for a dispatch inside its lock.

    Tiny offline fakes used by the synchronous worker tests predate the
    outbox model and intentionally expose no ``add``/``flush`` methods.  They
    still exercise dispatch transition guards, so absence of those methods is
    treated as a no-op compatibility boundary; real SQLAlchemy sessions always
    provide both and therefore enforce the unique source idempotency key.
    """

    add = getattr(db, "add", None)
    flush = getattr(db, "flush", None)
    if not callable(add) or not callable(flush):
        return None, False
    dialect = getattr(getattr(getattr(db, "bind", None), "dialect", None), "name", None)
    if dialect == "sqlite":
        bind = getattr(db, "bind", None)
        try:
            if bind is not None and not inspect(bind).has_table(
                IngestCompletionEvent.__tablename__
            ):
                return None, False
        except Exception:
            return None, False
    try:
        existing = db.scalar(
            select(IngestCompletionEvent)
            .where(IngestCompletionEvent.dispatch_id == dispatch.id)
            .with_for_update()
        )
    except Exception:
        # A few legacy SQLite management fixtures intentionally create only
        # the pre-outbox tables.  Keep those offline state-transition tests
        # meaningful; a real PostgreSQL session must surface schema drift.
        if dialect != "sqlite":
            raise
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            rollback()
        return None, False
    if existing is not None:
        return getattr(existing, "id", None), False
    event = IngestCompletionEvent(
        public_id=uuid4().hex,
        dispatch_id=dispatch.id,
        item_id=item.id,
        outcome=outcome,
        item_state=item_state,
        error_code=error_code,
        publish_state="pending",
    )
    add(event)
    flush()
    return getattr(event, "id", None), True


def _find_completion_event(db: Any, dispatch_id: int) -> Any | None:
    """Load one event for a locked dispatch when the session supports it."""

    if not callable(getattr(db, "scalar", None)):
        return None
    if not callable(getattr(db, "add", None)):
        return None
    dialect = getattr(getattr(getattr(db, "bind", None), "dialect", None), "name", None)
    if dialect == "sqlite":
        bind = getattr(db, "bind", None)
        try:
            if bind is not None and not inspect(bind).has_table(
                IngestCompletionEvent.__tablename__
            ):
                return None
        except Exception:
            return None
    return db.scalar(
        select(IngestCompletionEvent)
        .where(IngestCompletionEvent.dispatch_id == dispatch_id)
        .with_for_update()
    )


def _db_now(db: Any) -> datetime:
    """Read PostgreSQL time while keeping lightweight unit fakes usable."""

    try:
        value = db.scalar(select(func.now()))
    except Exception:
        value = None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _set_completion_statement_timeout(db: Any, budget_seconds: float) -> None:
    """Bound PostgreSQL outbox statements by the remaining sweep budget."""

    if budget_seconds <= 0:
        raise TimeoutError("completion_sweep_deadline")
    bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect == "postgresql":
        db.execute(
            text("SELECT set_config('statement_timeout', :timeout_text, true)"),
            {"timeout_text": f"{max(1, int(budget_seconds * 1000))}ms"},
        )


def _completion_claim_timeout_seconds() -> int:
    try:
        return max(1, int(get_settings().ingest_completion_claim_timeout_seconds))
    except (RuntimeError, ValueError, AttributeError):
        return 300


def _claim_completion_event(
    event_id: int,
    *,
    session_factory=None,
    claim_timeout_seconds: int | None = None,
) -> tuple[str, Any] | None:
    """Claim one pending/stale event in a short database transaction."""

    factory = session_factory or get_session_factory()
    timeout = max(
        1,
        int(
            claim_timeout_seconds
            if claim_timeout_seconds is not None
            else _completion_claim_timeout_seconds()
        ),
    )
    with factory() as db:
        event = db.scalar(
            select(IngestCompletionEvent)
            .where(IngestCompletionEvent.id == event_id)
            .with_for_update()
        )
        if event is None:
            return None
        now = _db_now(db)
        stale_before = now - timedelta(seconds=timeout)
        claimed_at = event.claimed_at
        if isinstance(claimed_at, datetime) and claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        stale = event.publish_state == "claimed" and (
            claimed_at is None or claimed_at <= stale_before
        )
        if event.publish_state == "enqueued":
            return None
        if event.publish_state not in {"pending", "claimed"}:
            return None
        if event.publish_state == "claimed" and not stale:
            return None
        token = uuid4().hex
        event.publish_state = "claimed"
        event.claim_token = token
        event.claimed_at = now
        event.updated_at = now
        db.commit()
        return token, event


def _release_completion_claim(
    event_id: int,
    claim_token: str,
    *,
    session_factory=None,
    budget_seconds: float | None = None,
) -> bool:
    factory = session_factory or get_session_factory()
    with factory() as db:
        if budget_seconds is not None:
            _set_completion_statement_timeout(db, budget_seconds)
        event = db.scalar(
            select(IngestCompletionEvent)
            .where(
                IngestCompletionEvent.id == event_id,
                IngestCompletionEvent.publish_state == "claimed",
                IngestCompletionEvent.claim_token == claim_token,
            )
            .with_for_update()
        )
        if event is None:
            return False
        now = _db_now(db)
        event.publish_state = "pending"
        event.claim_token = None
        event.claimed_at = None
        event.updated_at = now
        db.commit()
        return True


def _mark_completion_enqueued(
    event_id: int,
    claim_token: str,
    task_id: str | None,
    *,
    session_factory=None,
    budget_seconds: float | None = None,
) -> bool:
    """Ack only the outbox row that this publisher claimed."""

    factory = session_factory or get_session_factory()
    with factory() as db:
        if budget_seconds is not None:
            _set_completion_statement_timeout(db, budget_seconds)
        event = db.scalar(
            select(IngestCompletionEvent)
            .where(
                IngestCompletionEvent.id == event_id,
                IngestCompletionEvent.publish_state == "claimed",
                IngestCompletionEvent.claim_token == claim_token,
            )
            .with_for_update()
        )
        if event is None:
            return False
        now = _db_now(db)
        event.publish_state = "enqueued"
        event.claim_token = None
        event.claimed_at = None
        event.publish_task_id = task_id
        event.enqueued_at = now
        event.updated_at = now
        db.commit()
        return True


def publish_ingest_completion_event(
    event_id: int,
    *,
    session_factory=None,
    settings: Settings | None = None,
) -> str | None:
    """Publish one completion event using a bounded, durable task envelope.

    Only the internal event row ID is serialized.  The source dispatch/item
    transaction has already committed before this function is called.
    """

    settings = settings or get_settings()
    claim = _claim_completion_event(
        event_id,
        session_factory=session_factory,
        claim_timeout_seconds=settings.ingest_completion_claim_timeout_seconds,
    )
    if claim is None:
        return None
    claim_token, _event = claim
    options = _bounded_publish_options(settings)
    connect_timeout = options.pop("_connect_timeout")
    socket_timeout = options.pop("_socket_timeout")
    options.pop("_total_timeout")
    celery_app.conf.broker_connection_timeout = connect_timeout
    transport_options = dict(celery_app.conf.broker_transport_options or {})
    transport_options.update(
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
    )
    celery_app.conf.broker_transport_options = transport_options
    try:
        with celery_app.connection_for_write(
            connect_timeout=connect_timeout,
            transport_options=transport_options,
        ) as connection:
            producer = Producer(connection)
            result = celery_app.send_task(
                COMPLETION_TASK_NAME,
                args=[event_id],
                queue=COMPLETION_QUEUE,
                producer=producer,
                declare=[_COMPLETION_QUEUES[-1]],
                delivery_mode=2,
                **options,
            )
        task_id = str(getattr(result, "id", "") or "") or None
    except Exception:
        # Do not include broker/provider exception text in the runtime
        # diagnostic; the pending row is the durable recovery record.
        try:
            _release_completion_claim(
                event_id, claim_token, session_factory=session_factory
            )
        except Exception:
            # A DB outage after a broker failure leaves the claim for the
            # bounded stale-claim sweep; never mask the publish failure with
            # an adapter/driver exception.
            pass
        _completion_diagnostic(
            "completion_event_publish_failed",
            event_id=event_id,
            error_code="completion_publish_failed",
        )
        raise
    marked = _mark_completion_enqueued(
        event_id,
        claim_token,
        task_id,
        session_factory=session_factory,
    )
    if marked:
        _completion_diagnostic("completion_event_enqueued", event_id=event_id)
    return task_id


def _publish_completion_event_best_effort(
    event_id: int,
    *,
    session_factory=None,
    outcome: str | None = None,
    item_state: str | None = None,
) -> str | None:
    """Low-latency publish that cannot change ingestion result semantics."""

    try:
        publisher = publish_ingest_completion_event
        parameters = py_inspect.signature(publisher).parameters
        if "session_factory" in parameters:
            return publisher(event_id, session_factory=session_factory)
        # Small fakes often monkeypatch a one-argument publisher.  Keep that
        # test seam working without weakening the production session boundary.
        return publisher(event_id)
    except Exception:
        _completion_diagnostic(
            "completion_event_publish_failed",
            event_id=event_id,
            outcome=outcome,
            item_state=item_state,
            error_code="completion_publish_failed",
        )
        return None


def _mark_dispatch_failed(
    dispatch_id: int,
    exc: BaseException,
    *,
    task_id: str | None = None,
    session_factory=None,
) -> int | None:
    factory = session_factory or get_session_factory()
    error_code = (
        "transient_fetch_failed" if isinstance(exc, TransientFetchError)
        else "ingest_too_large" if isinstance(exc, IngestLimitExceeded)
        else "ingestion_failed"
    )
    event_id: int | None = None
    event_created = False
    event_outcome: str | None = None
    event_item_state: str | None = None
    event_error_code: str | None = None
    with factory() as db:
        dispatch = db.scalar(
            select(IngestDispatch)
            .where(IngestDispatch.id == dispatch_id)
            .with_for_update()
        )
        if dispatch is None:
            return None
        if task_id is not None and dispatch.task_id not in {None, task_id}:
            return None
        # A repeated final failure hook must reuse the original event and
        # snapshot.  It must never overwrite a stable error code or create a
        # second row for the same attempt.
        if dispatch.state == "completed":
            return None
        if dispatch.state == "failed":
            existing = _find_completion_event(db, dispatch.id)
            event_id = getattr(existing, "id", None) if existing is not None else None
            if existing is not None:
                event_outcome = getattr(existing, "outcome", "failed")
                event_item_state = getattr(existing, "item_state", "failed")
            if event_id is None:
                item = db.get(ContentItem, dispatch.item_id)
                if item is not None:
                    event_outcome = "failed"
                    event_item_state = "failed"
                    event_error_code = dispatch.error_code or error_code
                    event_id, event_created = _ensure_completion_event(
                        db,
                        dispatch,
                        item,
                        outcome=event_outcome,
                        item_state=event_item_state,
                        error_code=event_error_code,
                    )
            if event_id is not None:
                db.commit()
        else:
            item = db.get(ContentItem, dispatch.item_id)
            if (
                item is not None
                and item.state == "ready"
                and dispatch.state in {"running", "enqueued"}
            ):
                # process_item commits ready before _complete_dispatch obtains
                # its next transaction. A crash/failure in that window must
                # preserve the item truth and converge the dispatch to success.
                dispatch.state = "completed"
                dispatch.error_code = None
                dispatch.updated_at = datetime.now(UTC)
                event_outcome = "completed"
                event_item_state = "ready"
                event_id, event_created = _ensure_completion_event(
                    db,
                    dispatch,
                    item,
                    outcome=event_outcome,
                    item_state=event_item_state,
                    error_code=None,
                )
            else:
                dispatch.state = "failed"
                dispatch.error_code = error_code
                dispatch.updated_at = datetime.now(UTC)
                if item is not None:
                    item.state = "failed"
                    item.fail_reason = error_code
                    event_outcome = "failed"
                    event_item_state = "failed"
                    event_error_code = error_code
                    event_id, event_created = _ensure_completion_event(
                        db,
                        dispatch,
                        item,
                        outcome=event_outcome,
                        item_state=event_item_state,
                        error_code=event_error_code,
                    )
            db.commit()
    if event_id is not None:
        if event_created:
            _completion_diagnostic(
                "completion_event_created",
                event_id=event_id,
                outcome=event_outcome,
                item_state=event_item_state,
                error_code=event_error_code,
            )
        # Notification delivery is deliberately decoupled from this terminal
        # transaction and is picked up by the maintenance poller.
    return event_id


def ingest_url(url: str, *, user_id: int, why_saved: str | None = None, connector=None, embedder=None, object_store=None, session_factory=None) -> tuple[int, str]:
    connector = connector or _connector(url)
    item_id = create_item(url, user_id=user_id, why_saved=why_saved, connector=connector, session_factory=session_factory)
    try:
        state = process_item(item_id, connector=connector, embedder=embedder, object_store=object_store, session_factory=session_factory)
    except Exception as exc:
        _mark_failed(item_id, exc, session_factory=session_factory)
        raise
    return item_id, state


def _mark_failed(item_id: int, exc: BaseException, *, session_factory=None) -> None:
    factory = session_factory or get_session_factory()
    with factory() as db:
        item = db.get(ContentItem, item_id)
        if item is not None:
            item.state = "failed"
            item.fail_reason = (
                "transient_fetch_failed" if isinstance(exc, TransientFetchError)
                else "ingest_too_large" if isinstance(exc, IngestLimitExceeded)
                else "ingestion_failed"
            )
            db.commit()


def run_isolated_batch(items: list[Any], worker: Callable[[Any], Any], *, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep) -> list[Any]:
    """Run independent items; a throttled item never pauses or cancels peers."""
    results: list[Any] = [None] * len(items)
    pending = list(enumerate(items))
    for attempt in range(max_retries + 1):
        retry: list[tuple[int, Any]] = []
        for index, item in pending:
            try:
                results[index] = worker(item)
            except TransientFetchError:
                retry.append((index, item))
        if not retry or attempt == max_retries:
            break
        sleep(min(8 * 2**attempt, 600))
        pending = retry
    return results
