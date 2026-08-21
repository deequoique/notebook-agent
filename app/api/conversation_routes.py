"""Compatibility conversation and channel-link routes for the canonical app.

The browser application owns authentication and CSRF enforcement in
``app.api.app``.  This module only adapts the existing channel/link services
to that application; it does not define a second cookie, session, or origin
boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, AsyncIterator, Literal, Protocol
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    Security,
)
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, or_, select

from app.agent.types import AgentAnswer
from app.api.library_schemas import ErrorResponse
from app.channels.errors import IdentityError
from app.channels.identity import consume_link_token, create_link_token
from app.channels.service import _link_failure
from app.channels.types import ChannelEnvelope, TenantContext
from app.models import AppUser, ChannelIdentity, ConversationThread, ConversationTurn
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.web_auth import AuthenticatedWebSession

_MAX_CONVERSATION_ID = 128
_MAX_MESSAGE_ID = 128
_MAX_MESSAGE_TEXT = 16_000
_DEFAULT_HISTORY_LIMIT = 30
_MAX_HISTORY_LIMIT = 50
_WEB_AGENT_TIMEOUT_GRACE_SECONDS = 10.0
_STREAM_MEDIA_TYPE = "text/event-stream"
_STREAM_UNAVAILABLE_STATUS = 406
_PRIVATE_RESULT_KEYS = frozenset(
    {
        "id",
        "item_id",
        "segment_id",
        "app_user_id",
        "channel_identity_id",
        "session_id",
        "tenant_id",
    }
)

logger = logging.getLogger(__name__)

_SESSION_COOKIE_SCHEMA = APIKeyCookie(
    name="__Host-kb_session",
    scheme_name="SessionCookie",
    auto_error=False,
)
CsrfHeader = Annotated[
    str,
    Header(alias="X-CSRF-Token", min_length=1, max_length=200),
]


def _web_agent_transport_timeout(settings: object) -> float:
    """Cover the runtime's retrieval and answer stages plus dispatch overhead."""

    stage_timeout = float(getattr(settings, "agent_timeout_seconds", 30.0))
    return 2 * stage_timeout + _WEB_AGENT_TIMEOUT_GRACE_SECONDS


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=_MAX_MESSAGE_ID)
    text: str = Field(min_length=1, max_length=_MAX_MESSAGE_TEXT)


class LinkTokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_channel: str = Field(min_length=1, max_length=32)


class ConsumeLinkTokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class LinkTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class LinkedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linked: bool = True


class ConversationCitationResponse(BaseModel):
    """Browser-safe citation projection without internal row identifiers."""

    model_config = ConfigDict(extra="forbid")

    title: str
    excerpt: str
    url: str
    start_sec: float | None = None


class ConversationResponse(BaseModel):
    """Stable compatibility response for the retained conversation surface."""

    model_config = ConfigDict(extra="forbid")

    status: str
    text: str
    citations: list[ConversationCitationResponse] = Field(default_factory=list)
    action_results: list[dict] = Field(default_factory=list)
    thread_id: str | None = None
    error_code: str | None = None


class ConversationStreamEvent(BaseModel):
    """The small, public event envelope used by the browser SSE client.

    The event type and activity values are intentionally closed sets.  Agent
    provider chunks, tool arguments, diagnostics, and hidden reasoning never
    cross this boundary.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "started",
        "activity",
        "text_delta",
        "completed",
        "error",
        "cancelled",
    ]
    request_id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1, max_length=_MAX_MESSAGE_ID)
    sequence: int = Field(ge=1)
    activity: Literal[
        "preparing",
        "retrieving",
        "composing",
        "completed",
        "failed",
        "cancelled",
    ] | None = None
    text: str | None = None
    response: ConversationResponse | None = None
    error_code: str | None = None
    message: str | None = None


_STREAM_ACTIVITY_LABELS: dict[str, str] = {
    "preparing": "正在准备回答…",
    "retrieving": "正在检索资料库…",
    "composing": "正在整理答案…",
    "completed": "回答已完成",
    "failed": "这次检索未能完成",
    "cancelled": "请求已取消",
}
_SAFE_STREAM_ERROR_CODES = frozenset(
    {
        "answer_unavailable",
        "cancelled",
        "delete_in_progress",
        "identity_error",
        "link_account_disabled",
        "link_channel_current",
        "link_channel_unsupported",
        "link_merge_busy",
        "link_merge_conflict",
        "link_source_unbound",
        "link_token_expired",
        "link_token_invalid",
        "link_token_used",
        "link_usage",
        "no_evidence",
        "read_unavailable",
        "request_failed",
        "retrieval_unavailable",
        "runtime_error",
        "thread_missing",
        "timeout",
        "todo_incomplete",
        "web_login_unavailable",
        "web_login_usage",
    }
)


def _safe_stream_error_code(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_STREAM_ERROR_CODES else "request_failed"


def _safe_stream_response(response: ConversationResponse) -> ConversationResponse:
    """Keep response error metadata inside the public stream allow-list."""

    if response.error_code is None:
        return response
    return response.model_copy(
        update={"error_code": _safe_stream_error_code(response.error_code)}
    )


def _encode_stream_event(event: ConversationStreamEvent) -> str:
    """Serialize one public event as a complete SSE record."""

    payload = event.model_dump(mode="json", exclude_none=True)
    return (
        f"event: {event.type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class ConversationHistoryItemResponse(BaseModel):
    """A compact browser-safe projection for the conversation sidebar."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    conversation_id: str
    title: str
    preview: str
    updated_at: datetime


class ConversationHistoryPageResponse(BaseModel):
    """A bounded, opaque-cursor page of the current identity's threads."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationHistoryItemResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class ConversationTurnResponse(BaseModel):
    """One persisted public conversation turn without model/provider data."""

    model_config = ConfigDict(extra="forbid")

    user_text: str
    assistant_text: str
    status: str
    error_code: str | None = None
    citations: list[ConversationCitationResponse] = Field(default_factory=list)
    action_results: list[dict] = Field(default_factory=list)
    created_at: datetime


class ConversationTurnsResponse(BaseModel):
    """The saved transcript for one thread owned by the current identity."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    conversation_id: str
    turns: list[ConversationTurnResponse] = Field(default_factory=list)


@dataclass(frozen=True)
class BrowserSessionIdentity:
    """Public identity projection used by retained browser channel routes.

    The email session implementation exposes a :class:`TenantContext`, while
    the migration-era channel session exposes only ``app_user_id`` and
    ``login_channel``.  Keeping the identity projection explicit prevents the
    route from accidentally treating an internal numeric id as an external
    channel principal.
    """

    app_user_id: int
    external_user_id: str
    tenant: TenantContext


class BrowserSessionIdentityResolver(Protocol):
    def __call__(self, session: object) -> BrowserSessionIdentity: ...


def resolve_browser_session_identity(
    session: object,
    session_factory: Callable | None,
) -> BrowserSessionIdentity:
    """Adapt either canonical email or legacy channel sessions.

    Email sessions carry their already-validated tenant directly.  Legacy
    sessions deliberately do not, so resolve the channel identity server-side
    using the session's app user and login channel.  The query is deterministic
    and rejects disabled/missing identities rather than falling back to an
    internal id.
    """

    tenant = getattr(session, "tenant", None)
    if isinstance(tenant, TenantContext):
        return BrowserSessionIdentity(
            app_user_id=tenant.app_user_id,
            external_user_id=tenant.external_user_id,
            tenant=tenant,
        )

    app_user_id = getattr(session, "app_user_id", None)
    login_channel = str(getattr(session, "login_channel", "")).strip().lower()
    if (
        isinstance(app_user_id, bool)
        or not isinstance(app_user_id, int)
        or app_user_id <= 0
        or not login_channel
        or session_factory is None
    ):
        raise ValueError("browser session identity is unavailable")

    with session_factory() as db:
        user = db.get(AppUser, app_user_id)
        identity = db.scalar(
            select(ChannelIdentity)
            .where(
                ChannelIdentity.app_user_id == app_user_id,
                ChannelIdentity.channel == login_channel,
                ChannelIdentity.disabled_at.is_(None),
            )
            .order_by(ChannelIdentity.id)
        )
        if (
            user is None
            or user.disabled_at is not None
            or identity is None
            or identity.app_user_id != user.id
        ):
            raise ValueError("browser session identity is unavailable")
        resolved_tenant = TenantContext(
            app_user_id=user.id,
            channel_identity_id=identity.id,
            channel=identity.channel,
            account_id=identity.account_id,
            external_user_id=identity.external_user_id,
        )
    return BrowserSessionIdentity(
        app_user_id=resolved_tenant.app_user_id,
        external_user_id=resolved_tenant.external_user_id,
        tenant=resolved_tenant,
    )


def build_conversation_router(
    *,
    channel_service,
    session_dependency: Callable,
    session_factory,
    settings,
    session_identity_resolver: BrowserSessionIdentityResolver | None = None,
) -> APIRouter:
    """Build retained conversation/link routes behind canonical auth.

    ``channel_service`` may be ``None`` for API-only compositions such as the
    OpenAPI exporter.  The routes remain documented but fail closed with a
    bounded ``request_failed`` response until a service is configured.
    """

    router = APIRouter(prefix="/api/v1", tags=["conversation"])

    def authenticated_session(
        request: Request,
        _session_cookie: str | None = Security(_SESSION_COOKIE_SCHEMA),
    ) -> AuthenticatedWebSession:
        # The canonical application owns cookie parsing and session
        # resolution.  The wrapper exists only to document the shared cookie
        # security scheme on these compatibility operations.
        return session_dependency(request)

    def service_or_unavailable():
        if channel_service is None or session_factory is None:
            raise HTTPException(status_code=503, detail="request_failed")
        return channel_service

    def history_store_or_unavailable():
        if session_factory is None:
            raise HTTPException(status_code=503, detail="request_failed")
        return session_factory

    def browser_identity(session: object) -> BrowserSessionIdentity:
        if session_identity_resolver is not None:
            resolved = session_identity_resolver(session)
            if isinstance(resolved, BrowserSessionIdentity):
                return resolved
            # A resolver supplied by a small embedding may return the
            # canonical tenant directly; normalize it at this boundary.
            if isinstance(resolved, TenantContext):
                return BrowserSessionIdentity(
                    app_user_id=resolved.app_user_id,
                    external_user_id=resolved.external_user_id,
                    tenant=resolved,
                )
            raise ValueError("browser session identity resolver returned an invalid value")
        return resolve_browser_session_identity(session, session_factory)

    def project_answer(answer: AgentAnswer) -> ConversationResponse:
        return ConversationResponse(
            status=answer.status,
            text=answer.text,
            citations=[
                ConversationCitationResponse(
                    title=citation.title,
                    excerpt=citation.excerpt,
                    url=citation.url,
                    start_sec=citation.start_sec,
                )
                for citation in answer.citations
            ],
            action_results=[_safe_result(value) for value in answer.action_results],
            thread_id=answer.thread_id,
            error_code=answer.error_code,
        )

    def _safe_result(value):
        if isinstance(value, dict):
            return {
                key: _safe_result(item)
                for key, item in value.items()
                if key not in _PRIVATE_RESULT_KEYS
            }
        if isinstance(value, list):
            return [_safe_result(item) for item in value]
        return value

    def _safe_citations(value: object) -> list[ConversationCitationResponse]:
        """Project persisted JSON through the same public citation boundary.

        ``ConversationTurn.sources`` is intentionally flexible storage for the
        Agent runtime.  Do not return it directly: historical rows can contain
        internal retrieval identifiers alongside the user-facing citation.
        """

        if not isinstance(value, list):
            return []
        citations: list[ConversationCitationResponse] = []
        for source in value:
            if not isinstance(source, dict):
                continue
            title = source.get("title")
            excerpt = source.get("excerpt")
            url = source.get("url")
            if not all(isinstance(item, str) for item in (title, excerpt, url)):
                continue
            start_sec = source.get("start_sec")
            citations.append(
                ConversationCitationResponse(
                    title=title,
                    excerpt=excerpt,
                    url=url,
                    start_sec=float(start_sec) if isinstance(start_sec, (int, float)) else None,
                )
            )
        return citations

    def _thread_item(thread: ConversationThread, latest_turn: ConversationTurn | None) -> ConversationHistoryItemResponse:
        prompt = latest_turn.user_text.strip() if latest_turn is not None else ""
        title = prompt[:80] or "新对话"
        preview = (latest_turn.assistant_text.strip() if latest_turn is not None else "")[:160]
        return ConversationHistoryItemResponse(
            thread_id=thread.public_id,
            conversation_id=thread.external_conversation_id,
            title=title,
            preview=preview,
            updated_at=thread.updated_at,
        )

    def _owned_thread(db, identity: BrowserSessionIdentity, thread_id: str) -> ConversationThread | None:
        return db.scalar(
            select(ConversationThread).where(
                ConversationThread.public_id == thread_id,
                ConversationThread.app_user_id == identity.app_user_id,
                ConversationThread.channel_identity_id == identity.tenant.channel_identity_id,
            )
        )

    def web_envelope(
        session: object,
        conversation_id: str,
        message_id: str,
        text: str,
        *,
        request_id: str | None = None,
    ) -> ChannelEnvelope:
        identity = browser_identity(session)
        tenant = identity.tenant
        return ChannelEnvelope(
            tenant.channel,
            tenant.account_id,
            tenant.external_user_id,
            conversation_id,
            message_id,
            text,
            request_id=request_id or uuid4().hex,
        )

    @router.get(
        "/conversations",
        response_model=ConversationHistoryPageResponse,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_conversations(
        limit: int = _DEFAULT_HISTORY_LIMIT,
        cursor: str | None = None,
        session: object = Depends(authenticated_session),
    ) -> ConversationHistoryPageResponse:
        if not 1 <= limit <= _MAX_HISTORY_LIMIT:
            raise HTTPException(status_code=422, detail="validation_error")
        identity = browser_identity(session)
        factory = history_store_or_unavailable()
        with factory() as db:
            filters = (
                ConversationThread.app_user_id == identity.app_user_id,
                ConversationThread.channel_identity_id == identity.tenant.channel_identity_id,
            )
            statement = select(ConversationThread).where(*filters)
            if cursor:
                cursor_thread = _owned_thread(db, identity, cursor)
                if cursor_thread is None:
                    raise HTTPException(status_code=404, detail="not_found")
                statement = statement.where(
                    or_(
                        ConversationThread.updated_at < cursor_thread.updated_at,
                        and_(
                            ConversationThread.updated_at == cursor_thread.updated_at,
                            ConversationThread.id < cursor_thread.id,
                        ),
                    )
                )
            threads = list(
                db.scalars(
                    statement.order_by(
                        ConversationThread.updated_at.desc(), ConversationThread.id.desc()
                    ).limit(limit + 1)
                )
            )
            page_threads = threads[:limit]
            items = []
            for thread in page_threads:
                latest_turn = db.scalar(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.thread_id == thread.id,
                        ConversationTurn.status == "completed",
                    )
                    .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
                    .limit(1)
                )
                items.append(_thread_item(thread, latest_turn))
            return ConversationHistoryPageResponse(
                items=items,
                next_cursor=page_threads[-1].public_id if len(threads) > limit else None,
            )

    @router.get(
        "/conversations/{thread_id}/turns",
        response_model=ConversationTurnsResponse,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_conversation_turns(
        thread_id: str,
        session: object = Depends(authenticated_session),
    ) -> ConversationTurnsResponse:
        identity = browser_identity(session)
        factory = history_store_or_unavailable()
        with factory() as db:
            thread = _owned_thread(db, identity, thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="not_found")
            turns = list(
                db.scalars(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.thread_id == thread.id,
                        ConversationTurn.status == "completed",
                    )
                    .order_by(ConversationTurn.created_at, ConversationTurn.id)
                )
            )
            return ConversationTurnsResponse(
                thread_id=thread.public_id,
                conversation_id=thread.external_conversation_id,
                turns=[
                    ConversationTurnResponse(
                        user_text=turn.user_text,
                        assistant_text=turn.assistant_text,
                        status=turn.answer_status,
                        error_code=turn.error_code,
                        citations=_safe_citations(turn.sources),
                        action_results=[_safe_result(value) for value in turn.action_results]
                        if isinstance(turn.action_results, list)
                        else [],
                        created_at=turn.created_at,
                    )
                    for turn in turns
                ],
            )

    @router.delete(
        "/conversations/{thread_id}",
        status_code=204,
        response_class=Response,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def delete_conversation(
        thread_id: str,
        _csrf_token: CsrfHeader,
        session: object = Depends(authenticated_session),
    ) -> Response:
        """Permanently remove one thread owned by the current web identity."""

        identity = browser_identity(session)
        factory = history_store_or_unavailable()
        with factory() as db:
            thread = _owned_thread(db, identity, thread_id)
            if thread is None:
                # Do not disclose whether this public ID belongs to another
                # app user or a linked channel identity.
                raise HTTPException(status_code=404, detail="not_found")
            # The production foreign key also cascades this deletion.  Delete
            # turns explicitly so the durable deletion stays correct in
            # lightweight SQLite/test deployments where FK enforcement is off.
            db.execute(delete(ConversationTurn).where(ConversationTurn.thread_id == thread.id))
            db.delete(thread)
            db.commit()
        return Response(status_code=204)

    @router.post(
        "/conversations/{conversation_id}/messages",
        response_model=ConversationResponse,
        responses={
            401: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def send_message(
        conversation_id: str,
        payload: MessageInput,
        _csrf_token: CsrfHeader,
        session: object = Depends(authenticated_session),
    ) -> ConversationResponse:
        channel = service_or_unavailable()
        if not 1 <= len(conversation_id.strip()) <= _MAX_CONVERSATION_ID:
            raise HTTPException(status_code=422, detail="validation_error")
        try:
            # The legacy knowledge runtime owns two independently bounded
            # model stages: retrieval followed by answer composition.  Keep
            # this transport timeout outside both stage budgets so a retrieval
            # timeout with trusted evidence can still compose or fall back.
            answer = await asyncio.wait_for(
                channel.handle(
                    web_envelope(
                        session,
                        conversation_id,
                        payload.message_id,
                        payload.text,
                    )
                ),
                timeout=_web_agent_transport_timeout(settings),
            )
        except TimeoutError:
            raise HTTPException(status_code=504, detail="request_failed") from None
        return project_answer(answer)

    @router.post(
        "/conversations/{conversation_id}/messages/stream",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Server-sent public conversation events",
                "content": {"text/event-stream": {}},
            },
            401: {"model": ErrorResponse},
            406: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def send_message_stream(
        conversation_id: str,
        payload: MessageInput,
        _csrf_token: CsrfHeader,
        session: object = Depends(authenticated_session),
    ) -> StreamingResponse:
        """Stream a browser-safe lifecycle while retaining JSON compatibility.

        The channel service still owns identity resolution, idempotency,
        persistence, and answer projection.  This route only wraps that one
        execution in a bounded, typed SSE envelope; it never starts a second
        Agent task or persistence path.
        """

        channel = service_or_unavailable()
        if not 1 <= len(conversation_id.strip()) <= _MAX_CONVERSATION_ID:
            raise HTTPException(status_code=422, detail="validation_error")
        if not bool(getattr(settings, "agent_streaming_enabled", True)):
            raise HTTPException(
                status_code=_STREAM_UNAVAILABLE_STATUS,
                detail="streaming_disabled",
            )

        request_id = uuid4().hex
        envelope = web_envelope(
            session,
            conversation_id,
            payload.message_id,
            payload.text,
            request_id=request_id,
        )
        started_at = time.monotonic()

        async def events() -> AsyncIterator[str]:
            sequence = 0

            def next_event(
                event_type: Literal[
                    "started",
                    "activity",
                    "text_delta",
                    "completed",
                    "error",
                    "cancelled",
                ],
                *,
                activity: str | None = None,
                text: str | None = None,
                response: ConversationResponse | None = None,
                error_code: str | None = None,
                message: str | None = None,
            ) -> ConversationStreamEvent:
                nonlocal sequence
                sequence += 1
                safe_activity = (
                    activity if activity in _STREAM_ACTIVITY_LABELS else None
                )
                return ConversationStreamEvent(
                    type=event_type,
                    request_id=request_id,
                    message_id=payload.message_id,
                    sequence=sequence,
                    activity=safe_activity,
                    text=text,
                    response=response,
                    error_code=_safe_stream_error_code(error_code)
                    if error_code is not None
                    else None,
                    message=message,
                )

            def emit(
                event: ConversationStreamEvent,
                *,
                outcome: str | None = None,
            ) -> str:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.info(
                    "conversation_stream_lifecycle request_id=%s event_type=%s "
                    "outcome=%s elapsed_ms=%d",
                    request_id,
                    event.type,
                    outcome or "in_progress",
                    elapsed_ms,
                )
                return _encode_stream_event(event)

            yield emit(
                next_event(
                    "started",
                    activity="preparing",
                    message=_STREAM_ACTIVITY_LABELS["preparing"],
                )
            )
            yield emit(
                next_event(
                    "activity",
                    activity="retrieving",
                    message=_STREAM_ACTIVITY_LABELS["retrieving"],
                )
            )
            try:
                answer = await asyncio.wait_for(
                    channel.handle(envelope),
                    timeout=float(getattr(settings, "agent_timeout_seconds", 30.0)),
                )
                projected = _safe_stream_response(project_answer(answer))
                if projected.status == "failed":
                    yield emit(
                        next_event(
                            "activity",
                            activity="failed",
                            message=_STREAM_ACTIVITY_LABELS["failed"],
                        )
                    )
                    yield emit(
                        next_event(
                            "error",
                            activity="failed",
                            response=projected,
                            error_code=projected.error_code,
                            message="请求未能完成，请稍后重试。",
                        ),
                        outcome="failed",
                    )
                    return
                yield emit(
                    next_event(
                        "activity",
                        activity="composing",
                        message=_STREAM_ACTIVITY_LABELS["composing"],
                    )
                )
                # Provider-level token streaming is intentionally not
                # fabricated here.  The whole-answer compatibility service
                # produces one safe delta, which still exercises the same
                # public protocol and keeps persistence single-shot.
                if projected.text:
                    yield emit(
                        next_event("text_delta", text=projected.text)
                    )
                yield emit(
                    next_event(
                        "completed",
                        activity="completed",
                        response=projected,
                        message=_STREAM_ACTIVITY_LABELS["completed"],
                    ),
                    outcome="completed",
                )
            except asyncio.CancelledError:
                cancelled = ConversationResponse(
                    status="failed",
                    text="请求已取消。",
                    action_results=[],
                    error_code="cancelled",
                )
                # A disconnect may prevent this final record reaching the
                # client, but the event remains deterministic for direct
                # ASGI/test consumers and never leaves a pending UI state.
                yield emit(
                    next_event(
                        "cancelled",
                        activity="cancelled",
                        response=cancelled,
                        error_code="cancelled",
                        message=_STREAM_ACTIVITY_LABELS["cancelled"],
                    ),
                    outcome="cancelled",
                )
            except TimeoutError:
                failed = ConversationResponse(
                    status="failed",
                    text="请求超时，请稍后重试。",
                    action_results=[],
                    error_code="timeout",
                )
                yield emit(
                    next_event(
                        "error",
                        activity="failed",
                        response=failed,
                        error_code="timeout",
                        message="请求超时，请稍后重试。",
                    ),
                    outcome="timeout",
                )
            except Exception:
                # Do not copy exception details into the browser or logs.
                failed = ConversationResponse(
                    status="failed",
                    text="请求无法完成，请稍后重试。",
                    action_results=[],
                    error_code="request_failed",
                )
                yield emit(
                    next_event(
                        "error",
                        activity="failed",
                        response=failed,
                        error_code="request_failed",
                        message="请求无法完成，请稍后重试。",
                    ),
                    outcome="failed",
                )

        return StreamingResponse(
            events(),
            media_type=_STREAM_MEDIA_TYPE,
            headers={
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Agent-Streaming": "enabled",
            },
        )

    @router.post(
        "/conversations/{conversation_id}/reset",
        response_model=ConversationResponse,
        responses={
            401: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def reset_conversation(
        conversation_id: str,
        _csrf_token: CsrfHeader,
        session: object = Depends(authenticated_session),
    ) -> ConversationResponse:
        channel = service_or_unavailable()
        if not 1 <= len(conversation_id.strip()) <= _MAX_CONVERSATION_ID:
            raise HTTPException(status_code=422, detail="validation_error")
        answer = await channel.handle(
            web_envelope(
                session,
                conversation_id,
                f"web-reset-{uuid4().hex}",
                "/new",
            )
        )
        return project_answer(answer)

    @router.post(
        "/link-tokens",
        response_model=LinkTokenResponse,
        responses={
            401: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def create_token(
        payload: LinkTokenInput,
        _csrf_token: CsrfHeader,
        session: object = Depends(authenticated_session),
    ) -> LinkTokenResponse:
        service_or_unavailable()
        target = payload.target_channel.strip().lower()
        if target not in {"telegram", "wechat"}:
            raise HTTPException(status_code=422, detail="validation_error")
        try:
            with session_factory() as db:
                token = create_link_token(
                    db,
                    browser_identity(session).tenant,
                    target_channel=target,
                    ttl=timedelta(
                        seconds=float(
                            getattr(settings, "channel_link_ttl_seconds", 600)
                        )
                    ),
                )
                db.commit()
        except IdentityError as exc:
            raise HTTPException(
                status_code=409, detail=_link_failure(exc).error_code
            ) from None
        return LinkTokenResponse(token=token)

    @router.post(
        "/link-tokens/consume",
        response_model=LinkedResponse,
        responses={
            401: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def consume_token(
        payload: ConsumeLinkTokenInput,
        _csrf_token: CsrfHeader,
        response: Response,
        session: object = Depends(authenticated_session),
    ) -> LinkedResponse:
        service_or_unavailable()
        envelope = web_envelope(session, "web-link", f"web-link-{uuid4().hex}", "")
        try:
            with session_factory() as db:
                consume_link_token(db, envelope, payload.token)
                db.commit()
        except IdentityError as exc:
            raise HTTPException(
                status_code=409, detail=_link_failure(exc).error_code
            ) from None
        # Linking may absorb the presenting tenant; do not retain its session.
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(
            CSRF_COOKIE_NAME,
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )
        return LinkedResponse()

    return router


__all__ = [
    "BrowserSessionIdentity",
    "BrowserSessionIdentityResolver",
    "ConsumeLinkTokenInput",
    "ConversationCitationResponse",
    "ConversationHistoryItemResponse",
    "ConversationHistoryPageResponse",
    "ConversationResponse",
    "ConversationStreamEvent",
    "ConversationTurnResponse",
    "ConversationTurnsResponse",
    "LinkTokenInput",
    "LinkTokenResponse",
    "LinkedResponse",
    "MessageInput",
    "build_conversation_router",
    "resolve_browser_session_identity",
]
