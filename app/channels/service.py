"""Deterministic channel commands and the trusted Agent boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.context import ContextBuilder, TurnContext
from app.agent.runtime import KnowledgeAgent
from app.agent.streaming import AgentStreamEvent
from app.agent.types import AgentAnswer, AgentRequest, Citation
from app.channels.conversations import (
    ThreadResetBlocked,
    find_turn,
    get_or_create_thread,
    load_message_history,
    reset_thread,
    save_completed_turn,
)
from app.channels.errors import (
    DisabledIdentity,
    ExpiredLinkToken,
    IdentityConflict,
    IdentityError,
    InvalidLinkToken,
    LinkMergeBusy,
    UnboundIdentity,
    UsedLinkToken,
    WrongChannelLinkToken,
)
from app.channels.identity import (
    classify_link_argument,
    consume_link_token,
    create_link_token,
    resolve_identity,
    resolve_or_register,
)
from app.channels.types import ChannelEnvelope
from app.config import Settings
from app.diagnostics import RequestDiagnostics
from app.models import ConversationThread, ConversationTurn
from app.web.auth import WebAuthError, WebAuthService


class ChannelService:
    """Handle one normalized message without importing any platform SDK."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        agent: KnowledgeAgent,
        settings: Settings,
        *,
        web_auth: WebAuthService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent
        self._settings = settings
        self._web_auth = web_auth
        self._locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}
        self._context_builder = ContextBuilder(
            max_turns=getattr(settings, "context_max_turns", 8),
            # Keep the context projection independent from the larger
            # model message history budget; these are deliberately small
            # row caps.
        )

    async def handle(self, envelope: ChannelEnvelope) -> AgentAnswer:
        # Every in-process entry point gets an internal correlation ID. The
        # HTTP gateway overwrites its untrusted envelope field before this
        # point; other adapters and CLI receive a locally generated one.
        if envelope.request_id is None:
            envelope = replace(envelope, request_id=uuid4().hex)
        key = (
            envelope.channel,
            envelope.account_id,
            envelope.external_user_id,
            envelope.conversation_id,
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                return await self._handle_locked(envelope)
            except IdentityError as exc:
                return AgentAnswer(
                    status="failed",
                    text=f"身份验证失败：{exc}",
                    error_code="identity_error",
                )

    async def handle_stream(
        self, envelope: ChannelEnvelope
    ) -> AsyncIterator[AgentStreamEvent]:
        """Execute one turn and expose the validated Agent section stream.

        The lock, duplicate lookup, and final turn write remain here. The
        HTTP adapter only serializes these events and cannot create a second
        Agent or persistence path when a stream is unavailable.
        """

        if envelope.request_id is None:
            envelope = replace(envelope, request_id=uuid4().hex)
        key = (
            envelope.channel,
            envelope.account_id,
            envelope.external_user_id,
            envelope.conversation_id,
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                async for event in self._handle_locked_stream(envelope):
                    yield event
            except IdentityError:
                yield AgentStreamEvent(
                    "completed",
                    envelope.request_id,
                    envelope.message_id,
                    answer=AgentAnswer(
                        status="failed",
                        text="身份验证失败，请稍后重试。",
                        error_code="identity_error",
                    ),
                )

    async def _handle_locked_stream(
        self, envelope: ChannelEnvelope
    ) -> AsyncIterator[AgentStreamEvent]:
        command, argument = _command(envelope.text)

        def completed(answer: AgentAnswer, new_messages=(), *, persist: bool = False):
            return AgentStreamEvent(
                "completed",
                envelope.request_id,
                envelope.message_id,
                answer=answer,
                new_messages=tuple(new_messages),
                persist=persist,
            )

        if command == "link":
            yield completed(self._handle_link(envelope, argument), persist=False)
            return
        if command == "web-login" and not argument:
            yield completed(
                AgentAnswer(
                    status="failed",
                    text="用法：发送 /web-login 登录码。",
                    error_code="web_login_usage",
                ),
                persist=False,
            )
            return

        with self._session_factory() as db:
            tenant = resolve_or_register(db, envelope)
            diagnostics = RequestDiagnostics.start(
                envelope.request_id,
                tenant.app_user_id,
                envelope.trace_id,
                allow_retrieval_content=self._settings.notebook_agent_log_retrieval_content,
                environment=self._settings.notebook_agent_env,
            )
            diagnostics.event("accepted")
            if command == "web-login":
                db.commit()
                if self._web_auth is None:
                    yield completed(
                        AgentAnswer(
                            status="failed",
                            text="网页登录当前不可用，请稍后重试。",
                            error_code="web_login_unavailable",
                        ),
                        persist=False,
                    )
                    return
                try:
                    self._web_auth.approve(argument, tenant)
                except WebAuthError as exc:
                    yield completed(
                        AgentAnswer(
                            status="failed", text=str(exc), error_code=exc.code
                        ),
                        persist=False,
                    )
                    return
                yield completed(
                    AgentAnswer(status="ok", text="网页登录已批准，请返回浏览器继续。")
                )
                return
            if command == "start":
                db.commit()
                yield completed(
                    AgentAnswer(
                        status="ok",
                        text=(
                            "账户已就绪。你的知识库与其他用户完全隔离。\n"
                            f"内部用户编号：{tenant.app_user_id}"
                        ),
                    ),
                    persist=False,
                )
                return
            if command == "whoami":
                db.commit()
                yield completed(
                    AgentAnswer(status="ok", text=f"内部用户编号：{tenant.app_user_id}"),
                    persist=False,
                )
                return
            if command == "new":
                try:
                    thread = reset_thread(db, tenant, envelope)
                except ThreadResetBlocked:
                    db.rollback()
                    yield completed(
                        AgentAnswer(
                            status="failed",
                            text="删除操作正在处理中，暂时无法开启新会话，请稍后重试。",
                            error_code="delete_in_progress",
                        ),
                        persist=False,
                    )
                    return
                db.commit()
                yield completed(
                    AgentAnswer(
                        status="ok",
                        text="已开启新会话，旧上下文不会继续带入。",
                        thread_id=thread.public_id,
                    ),
                    persist=False,
                )
                return

            thread = get_or_create_thread(db, tenant, envelope)
            duplicate = find_turn(db, thread.id, envelope.message_id)
            if duplicate is not None:
                diagnostics.event("duplicate", route="duplicate")
                answer = _answer_from_turn(duplicate, thread.public_id)
                db.commit()
                yield completed(answer, persist=False)
                return
            history = load_message_history(
                db,
                thread.id,
                max_turns=self._settings.context_max_turns,
                max_tokens=self._settings.context_token_budget,
            )
            latest_turn = db.scalar(
                select(ConversationTurn)
                .where(
                    ConversationTurn.thread_id == thread.id,
                    ConversationTurn.status == "completed",
                )
                .order_by(
                    ConversationTurn.created_at.desc(),
                    ConversationTurn.id.desc(),
                )
                .limit(1)
            )
            context = self._context_builder.build(db, thread, tenant)
            request = AgentRequest(
                question=envelope.text,
                tenant=tenant,
                thread_db_id=thread.id,
                thread_public_id=thread.public_id,
                message_id=envelope.message_id,
                request_id=envelope.request_id,
                history=tuple(ModelMessagesTypeAdapter.dump_python(history, mode="json")),
                latest_turn_message_id=(
                    latest_turn.message_id if latest_turn is not None else None
                ),
                context=context,
            )
            db.commit()

        diagnostics.event("route", route="agent")
        async for event in self._agent.stream(request, diagnostics=diagnostics):
            if event.type != "completed" or event.answer is None or not event.persist:
                yield event
                continue
            answer = event.answer
            if answer.status == "failed" and not answer.action_results:
                yield event
                continue
            with self._session_factory() as db:
                current_thread = db.get(ConversationThread, request.thread_db_id)
                if (
                    current_thread is None
                    or current_thread.app_user_id != request.tenant.app_user_id
                ):
                    yield completed(
                        AgentAnswer(
                            status="failed",
                            text="会话已失效，请重新发送消息。",
                            error_code="thread_missing",
                        )
                    )
                    return
                duplicate = find_turn(db, current_thread.id, envelope.message_id)
                if duplicate is not None:
                    answer = _answer_from_turn(duplicate, current_thread.public_id)
                else:
                    save_completed_turn(
                        db,
                        thread=current_thread,
                        envelope=envelope,
                        assistant_text=answer.text,
                        sources=[value.model_dump() for value in answer.citations],
                        model_messages=event.new_messages,
                        answer_status=answer.status,
                        error_code=answer.error_code,
                        action_results=answer.action_results,
                    )
                    db.commit()
            yield completed(answer, event.new_messages)

    async def _handle_locked(self, envelope: ChannelEnvelope) -> AgentAnswer:
        command, argument = _command(envelope.text)
        if command == "link":
            return self._handle_link(envelope, argument)

        if command == "web-login" and not argument:
            return AgentAnswer(
                status="failed",
                text="用法：发送 /web-login 登录码。",
                error_code="web_login_usage",
            )

        with self._session_factory() as db:
            tenant = resolve_or_register(db, envelope)
            diagnostics = RequestDiagnostics.start(
                envelope.request_id,
                tenant.app_user_id,
                envelope.trace_id,
                allow_retrieval_content=self._settings.notebook_agent_log_retrieval_content,
                environment=self._settings.notebook_agent_env,
            )
            diagnostics.event("accepted")
            if command == "web-login":
                diagnostics.event("route", route="command")
                db.commit()
                if self._web_auth is None:
                    return self._response_ready(
                        diagnostics,
                        AgentAnswer(
                            status="failed",
                            text="网页登录当前不可用，请稍后重试。",
                            error_code="web_login_unavailable",
                        ),
                    )
                try:
                    self._web_auth.approve(argument, tenant)
                except WebAuthError as exc:
                    return self._response_ready(
                        diagnostics,
                        AgentAnswer(
                            status="failed",
                            text=str(exc),
                            error_code=exc.code,
                        ),
                    )
                return self._response_ready(
                    diagnostics,
                    AgentAnswer(
                        status="ok",
                        text="网页登录已批准，请返回浏览器继续。",
                    ),
                )
            if command == "start":
                diagnostics.event("route", route="command")
                db.commit()
                return self._response_ready(
                    diagnostics,
                    AgentAnswer(
                        status="ok",
                        text=(
                            "账户已就绪。你的知识库与其他用户完全隔离。\n"
                            f"内部用户编号：{tenant.app_user_id}"
                        ),
                    ),
                )
            if command == "whoami":
                diagnostics.event("route", route="command")
                db.commit()
                return self._response_ready(
                    diagnostics,
                    AgentAnswer(
                        status="ok", text=f"内部用户编号：{tenant.app_user_id}"
                    ),
                )
            if command == "new":
                diagnostics.event("route", route="command")
                try:
                    thread = reset_thread(db, tenant, envelope)
                except ThreadResetBlocked:
                    db.rollback()
                    return self._response_ready(
                        diagnostics,
                        AgentAnswer(
                            status="failed",
                            text="删除操作正在处理中，暂时无法开启新会话，请稍后重试。",
                            error_code="delete_in_progress",
                        ),
                    )
                db.commit()
                return self._response_ready(
                    diagnostics,
                    AgentAnswer(
                        status="ok",
                        text="已开启新会话，旧上下文不会继续带入。",
                        thread_id=thread.public_id,
                    ),
                )

            thread = get_or_create_thread(db, tenant, envelope)
            duplicate = find_turn(db, thread.id, envelope.message_id)
            if duplicate is not None:
                diagnostics.event("duplicate", route="duplicate")
                answer = _answer_from_turn(duplicate, thread.public_id)
                db.commit()
                return self._response_ready(diagnostics, answer)
            history = load_message_history(
                db,
                thread.id,
                max_turns=self._settings.context_max_turns,
                max_tokens=self._settings.context_token_budget,
            )
            latest_turn = db.scalar(
                select(ConversationTurn)
                .where(
                    ConversationTurn.thread_id == thread.id,
                    ConversationTurn.status == "completed",
                )
                .order_by(
                    ConversationTurn.created_at.desc(),
                    ConversationTurn.id.desc(),
                )
                .limit(1)
            )
            context = (
                self._context_builder.build(db, thread, tenant)
                if self._context_builder is not None
                else TurnContext()
            )
            request = AgentRequest(
                question=envelope.text,
                tenant=tenant,
                thread_db_id=thread.id,
                thread_public_id=thread.public_id,
                message_id=envelope.message_id,
                request_id=envelope.request_id,
                history=tuple(
                    ModelMessagesTypeAdapter.dump_python(history, mode="json")
                ),
                latest_turn_message_id=(
                    latest_turn.message_id if latest_turn is not None else None
                ),
                context=context,
            )
            db.commit()

        diagnostics.event("route", route="agent")
        execution = await self._agent.run(request, diagnostics=diagnostics)
        # A terminal action outcome must be durable even when its public status
        # is failed; plain transient knowledge failures remain retryable.
        if (
            execution.answer.status == "failed"
            and not execution.answer.action_results
        ):
            return self._response_ready(diagnostics, execution.answer)

        with self._session_factory() as db:
            thread = db.get(ConversationThread, request.thread_db_id)
            if thread is None or thread.app_user_id != request.tenant.app_user_id:
                return self._response_ready(
                    diagnostics,
                    AgentAnswer(
                        status="failed",
                        text="会话已失效，请重新发送消息。",
                        error_code="thread_missing",
                    ),
                )
            duplicate = find_turn(db, thread.id, envelope.message_id)
            if duplicate is not None:
                return self._response_ready(
                    diagnostics, _answer_from_turn(duplicate, thread.public_id)
                )
            save_completed_turn(
                db,
                thread=thread,
                envelope=envelope,
                assistant_text=execution.answer.text,
                sources=[value.model_dump() for value in execution.answer.citations],
                model_messages=execution.new_messages,
                answer_status=execution.answer.status,
                error_code=execution.answer.error_code,
                action_results=execution.answer.action_results,
            )
            db.commit()
        return self._response_ready(diagnostics, execution.answer)

    @staticmethod
    def _response_ready(
        diagnostics: RequestDiagnostics, answer: AgentAnswer
    ) -> AgentAnswer:
        diagnostics.event("gateway_response_ready", error_code=answer.error_code)
        return answer

    def _handle_link(self, envelope: ChannelEnvelope, argument: str | None) -> AgentAnswer:
        if not argument:
            return AgentAnswer(
                status="failed",
                text="用法：发送 /link telegram、/link wechat、/link web 或 /link <绑定码>。",
                error_code="link_usage",
            )
        try:
            kind, value = classify_link_argument(argument)
        except IdentityConflict:
            return AgentAnswer(
                status="failed",
                text="目前只支持 Telegram、微信与 Web 之间绑定。",
                error_code="link_channel_unsupported",
            )
        except InvalidLinkToken:
            return AgentAnswer(
                status="failed",
                text="绑定码格式无效，请从来源渠道重新生成。",
                error_code="link_token_invalid",
            )
        with self._session_factory() as db:
            if kind == "token":
                try:
                    consume_link_token(db, envelope, value)
                except IdentityError as exc:
                    return _link_failure(exc)
                db.commit()
                return AgentAnswer(
                    status="ok",
                    text="渠道绑定成功。两个渠道现在共享同一个私有知识库，聊天历史仍各自独立。",
                )
            try:
                tenant = resolve_identity(db, envelope)
                if value == envelope.channel:
                    return AgentAnswer(
                        status="failed",
                        text="目标渠道必须与当前渠道不同。",
                        error_code="link_channel_current",
                    )
                token = create_link_token(
                    db,
                    tenant,
                    target_channel=value,
                    ttl=timedelta(seconds=self._settings.channel_link_ttl_seconds),
                )
            except IdentityError as exc:
                return _link_failure(exc)
            db.commit()
            ttl_minutes = max(
                1, (self._settings.channel_link_ttl_seconds + 59) // 60
            )
            return AgentAnswer(
                status="ok",
                text=(
                    f"绑定码：{token}\n"
                    + ("请在已登录的 Web 页面中使用该绑定码。" if value == "web" else f"请在 {value} 中发送 /link {token}。")
                    + f"该绑定码约 {ttl_minutes} 分钟内有效且只能使用一次。"
                ),
            )


def _link_failure(exc: IdentityError) -> AgentAnswer:
    if isinstance(exc, UsedLinkToken):
        text, code = "该绑定码已使用，请重新生成。", "link_token_used"
    elif isinstance(exc, ExpiredLinkToken):
        text, code = "该绑定码已过期，请重新生成。", "link_token_expired"
    elif isinstance(exc, WrongChannelLinkToken):
        text, code = "请在绑定码指定的目标渠道中使用。", "link_channel_mismatch"
    elif isinstance(exc, LinkMergeBusy):
        text, code = "目标账户仍有内容正在处理，请稍后用同一绑定码重试。", "link_merge_busy"
    elif isinstance(exc, DisabledIdentity):
        text, code = "来源或目标账户已停用，无法绑定。", "link_account_disabled"
    elif isinstance(exc, UnboundIdentity):
        text, code = "请先在当前来源渠道发送 /start。", "link_source_unbound"
    elif isinstance(exc, IdentityConflict):
        text, code = "账户状态发生变化，请稍后重试或重新生成绑定码。", "link_merge_conflict"
    else:
        text, code = "绑定码无效，请重新生成。", "link_token_invalid"
    return AgentAnswer(status="failed", text=text, error_code=code)


def _command(text: str) -> tuple[str | None, str | None]:
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return None, None
    command = parts[0].split("@", 1)[0].lower().removeprefix("/")
    if command not in {"start", "new", "link", "whoami", "web-login"}:
        return None, None
    return command, parts[1].strip() if len(parts) == 2 else None


def _answer_from_turn(turn, public_id: str) -> AgentAnswer:
    citations = [Citation.model_validate(value) for value in turn.sources]
    answer_status = turn.answer_status
    error_code = turn.error_code
    if answer_status == "legacy":
        answer_status = "ok" if citations else "not_found"
        error_code = None if citations else "no_evidence"
    return AgentAnswer(
        status=answer_status,
        text=turn.assistant_text,
        citations=citations,
        action_results=list(turn.action_results or []),
        thread_id=public_id,
        error_code=error_code,
    )
