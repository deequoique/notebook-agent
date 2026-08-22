from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ModelMessagesTypeAdapter,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.agent.actions import AgentActionServices
from app.agent.management import KnowledgeItemManagementService
from app.agent.runtime import AgentExecution, KnowledgeAgent
from app.agent.services import KnowledgeNotFound, KnowledgeServices
from app.agent.types import AgentAnswer, AgentRequest, Citation
from app.channels.conversations import (
    get_or_create_thread,
    load_message_history,
    save_completed_turn,
)
from app.channels.errors import (
    DisabledIdentity,
    ExpiredLinkToken,
    InvalidLinkToken,
    LinkMergeBusy,
    UsedLinkToken,
)
from app.channels.http_gateway import RequestVerifier, signature
from app.channels.identity import (
    classify_link_argument,
    consume_link_token,
    create_link_token,
    resolve_identity,
    resolve_or_register,
)
from app.channels.pending_actions import PendingConfirmationService
from app.channels.service import ChannelService
from app.channels.types import ChannelEnvelope
from app.config import Settings
from app.db import get_engine, get_session_factory
from app.ingest.submission import IngestSubmissionService
from app.models import (
    AppUser,
    ChannelIdentity,
    ChannelLinkToken,
    ContentItem,
    ConversationThread,
    IngestDispatch,
    PendingChannelAction,
    Segment,
)
from app.retrieval.search import vector_search


class FakeEmbeddingProvider:
    dimensions = 1536

    def __init__(self):
        self.queries = []

    def embed(self, texts):
        self.queries.extend(texts)
        return [[0.01] * self.dimensions for _ in texts]


@pytest.fixture
def db_factory():
    try:
        connection = get_engine().connect()
        connection.execute(text("SELECT 1 FROM channel_identity LIMIT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL integration database unavailable: {type(exc).__name__}")
    transaction = connection.begin_nested() if connection.in_transaction() else connection.begin()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


def envelope(channel="telegram", user=None, message=None, conversation="chat"):
    suffix = user or uuid4().hex
    return ChannelEnvelope(
        channel=channel,
        account_id=f"account-{channel}",
        external_user_id=suffix,
        conversation_id=conversation,
        message_id=message or uuid4().hex,
        text="question",
    )


def _save_action_model(messages, info):
    last_request = next(
        message
        for message in reversed(messages)
        if isinstance(message, ModelRequest)
    )
    if any(isinstance(part, ToolReturnPart) for part in last_request.parts):
        return ModelResponse(parts=[TextPart("discarded model action draft")])
    prompt = next(
        str(part.content)
        for part in last_request.parts
        if isinstance(part, UserPromptPart)
    )
    urls = re.findall(r"https?://[^\s<>]+", prompt)
    if prompt.strip() == "需要":
        assert "可信服务器状态：当前 conversation 有" in info.instructions
        tool_name, arguments = "confirm_video_save", {}
    elif prompt.strip() == "确认":
        tool_name, arguments = "confirm_video_save", {}
    elif prompt.strip() == "取消":
        tool_name, arguments = "cancel_video_save", {}
    elif len(urls) == 1 and prompt.strip() == urls[0]:
        tool_name = "request_save_confirmation"
        arguments = {"urls": urls}
    else:
        tool_name = "save_videos"
        arguments = {"urls": urls, "why_saved": None}
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name,
                json.dumps(arguments, ensure_ascii=False),
                tool_call_id=f"{tool_name}-call",
            )
        ]
    )


async def _signed_channel_handle(service, verifier, channel_envelope):
    payload = {
        "channel": channel_envelope.channel,
        "account_id": channel_envelope.account_id,
        "external_user_id": channel_envelope.external_user_id,
        "conversation_id": channel_envelope.conversation_id,
        "message_id": channel_envelope.message_id,
        "text": channel_envelope.text,
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    assert verifier.verify(
        body,
        timestamp,
        nonce,
        signature("s" * 32, body, timestamp, nonce),
    )
    return await service.handle(ChannelEnvelope(**json.loads(body)))


@pytest.mark.asyncio
async def test_signed_save_actions_are_durable_and_exactly_once(db_factory):
    with db_factory() as db:
        item_ids_before = set(db.scalars(select(ContentItem.id)))

    published = []
    model_calls = []
    action_services = AgentActionServices(
        submission=IngestSubmissionService(
            db_factory,
            lambda dispatch_id: published.append(dispatch_id)
            or f"task-{dispatch_id}",
        ),
        pending=PendingConfirmationService(db_factory),
    )

    def model(messages, info):
        model_calls.append(1)
        return _save_action_model(messages, info)

    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(db_factory, agent, settings)
    verifier = RequestVerifier("s" * 32)
    external_user = f"save-user-{uuid4().hex}"

    def message(message_id, text_value):
        return ChannelEnvelope(
            channel="telegram",
            account_id="save-account",
            external_user_id=external_user,
            conversation_id="save-chat",
            message_id=message_id,
            text=text_value,
        )

    explicit_envelope = message(
        "m-explicit",
        "帮我保存 https://youtu.be/dQw4w9WgXcQ",
    )
    explicit = await _signed_channel_handle(
        service, verifier, explicit_envelope
    )
    assert explicit.status == "ok"
    assert explicit.error_code == "save_accepted"
    assert [row["status"] for row in explicit.action_results] == ["queued"]
    assert len(published) == 1
    calls_after_explicit = len(model_calls)

    explicit_replay = await _signed_channel_handle(
        service, verifier, explicit_envelope
    )
    assert explicit_replay.model_dump() == explicit.model_dump()
    assert len(published) == 1
    assert len(model_calls) == calls_after_explicit

    bare = await _signed_channel_handle(
        service,
        verifier,
        message("m-bare", "https://youtu.be/9bZkp7q19f0"),
    )
    assert bare.status == "ok"
    assert bare.error_code == "save_confirmation_required"
    assert len(published) == 1

    confirmed = await _signed_channel_handle(
        service, verifier, message("m-confirm", "需要")
    )
    assert confirmed.status == "ok"
    assert confirmed.error_code == "save_accepted"
    assert len(published) == 2

    await _signed_channel_handle(
        service,
        verifier,
        message("m-cancel-bare", "https://youtu.be/M7lc1UVf-VE"),
    )
    cancelled = await _signed_channel_handle(
        service, verifier, message("m-cancel", "取消")
    )
    assert cancelled.status == "ok"
    assert cancelled.error_code == "save_cancelled"
    assert len(published) == 2

    await _signed_channel_handle(
        service,
        verifier,
        message("m-expired-bare", "https://youtu.be/aqz-KE-bpKQ"),
    )
    with db_factory() as db:
        pending = db.scalar(
            select(PendingChannelAction)
            .where(
                PendingChannelAction.consumed_at.is_(None),
                PendingChannelAction.cancelled_at.is_(None),
            )
            .order_by(PendingChannelAction.id.desc())
            .limit(1)
        )
        assert pending is not None
        pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired_envelope = message("m-expired", "确认")
    expired = await _signed_channel_handle(
        service, verifier, expired_envelope
    )
    assert expired.status == "failed"
    assert expired.error_code == "confirmation_expired"
    expired_model_calls = len(model_calls)
    expired_replay = await _signed_channel_handle(
        service, verifier, expired_envelope
    )
    assert expired_replay.model_dump() == expired.model_dump()
    assert len(model_calls) == expired_model_calls
    assert len(published) == 2

    partial = await _signed_channel_handle(
        service,
        verifier,
        message(
            "m-partial",
            "保存 https://youtu.be/ScMzIvxBSi4 "
            "https://example.test/video",
        ),
    )
    assert partial.status == "ok"
    assert partial.error_code == "save_partial"
    assert [row["status"] for row in partial.action_results] == [
        "queued",
        "unsupported_url",
    ]
    assert len(published) == 3

    with db_factory() as db:
        counts_before = (
            len(list(db.scalars(select(ContentItem)))),
            len(list(db.scalars(select(IngestDispatch)))),
        )
    urls = [
        f"https://youtu.be/video{index:06d}" for index in range(11)
    ]
    too_large_envelope = message(
        "m-too-large", f"保存 {' '.join(urls)}"
    )
    too_large = await _signed_channel_handle(
        service, verifier, too_large_envelope
    )
    assert too_large.status == "failed"
    assert too_large.error_code == "batch_too_large"
    too_large_model_calls = len(model_calls)
    too_large_replay = await _signed_channel_handle(
        service, verifier, too_large_envelope
    )
    assert too_large_replay.model_dump() == too_large.model_dump()
    assert len(model_calls) == too_large_model_calls
    assert len(published) == 3

    with db_factory() as db:
        counts_after = (
            len(list(db.scalars(select(ContentItem)))),
            len(list(db.scalars(select(IngestDispatch)))),
        )
        identity = db.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == "telegram",
                ChannelIdentity.account_id == "save-account",
                ChannelIdentity.external_user_id == external_user,
            )
        )
        assert identity is not None
        owners = set(
            db.scalars(
                select(ContentItem.user_id).where(
                    ContentItem.id.not_in(item_ids_before)
                )
            )
        )
    assert counts_after == counts_before
    assert owners == {identity.app_user_id}


@pytest.mark.asyncio
async def test_channel_delete_confirmation_chain_survives_real_clarification(db_factory):
    """A clarification turn advances the server anchor before confirmation.

    This intentionally runs through ``ChannelService`` and persisted
    ``ConversationTurn`` rows rather than calling the pending service
    directly. A delayed confirmation must be accepted only when the latest
    completed turn is B (the clarification), while the target remains the
    server-owned payload created by A.
    """

    external_user = f"delete-chain-{uuid4().hex}"
    first = ChannelEnvelope(
        channel="telegram",
        account_id="delete-chain-account",
        external_user_id=external_user,
        conversation_id="delete-chain-chat",
        message_id="delete-A",
        text="删除这个条目",
    )
    with db_factory() as db:
        tenant = resolve_or_register(db, first)
        item = ContentItem(
            user_id=tenant.app_user_id,
            platform="youtube",
            platform_id=f"delete-chain-{uuid4().hex[:10]}",
            kind="video",
            url="https://youtu.be/delete-chain",
            title="delete-chain",
            text_source="none",
            state="ready",
        )
        db.add(item)
        db.commit()
        item_id = item.id

    action_services = AgentActionServices(
        submission=IngestSubmissionService(db_factory, lambda _dispatch_id: "task"),
        pending=PendingConfirmationService(db_factory),
        management=KnowledgeItemManagementService(db_factory),
    )

    def model(messages, _info):
        last_request = next(
            message
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
        )
        if any(isinstance(part, ToolReturnPart) for part in last_request.parts):
            return ModelResponse(parts=[TextPart("discarded action prose")])
        prompt = next(
            str(part.content)
            for part in last_request.parts
            if isinstance(part, UserPromptPart)
        )
        if prompt == "删除这个条目":
            tool_name, arguments = "delete_saved_items", {"item_ids": [item_id]}
        elif prompt == "我还不确定":
            tool_name, arguments = "clarify_item_deletion", {}
        elif prompt == "确认" or prompt.startswith("确认删除"):
            tool_name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name,
                    json.dumps(arguments),
                    tool_call_id=f"{tool_name}-{prompt}",
                )
            ]
        )

    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(db_factory, agent, settings)

    first_answer = await service.handle(first)
    assert first_answer.status == "ok"
    assert first_answer.error_code == "confirmation_required"
    assert first_answer.action_results == [{"status": "confirmation_required", "count": 1}]
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", first_answer.text)
    assert code_match is not None
    confirmation_code = code_match.group(1)

    second_answer = await service.handle(
        replace(first, message_id="delete-B", text="我还不确定")
    )
    assert second_answer.status == "ok"
    assert second_answer.error_code == "confirmation_required"
    with db_factory() as db:
        assert db.get(ContentItem, item_id).deleted_at is None
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id == "delete-chain-chat",
            )
        )
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert action is not None and action.consumed_at is None
        assert action.payload["confirmation_anchor_message_id"] == "delete-B"
        assert action.payload["confirmation_anchor_parent_message_id"] == "delete-A"

    third_answer = await service.handle(
        replace(first, message_id="delete-C", text=f"确认删除 {confirmation_code}")
    )
    assert third_answer.status == "ok"
    assert third_answer.error_code == "items_deleted"
    assert third_answer.action_results[0]["status"] == "deleted"
    with db_factory() as db:
        deleted = db.get(ContentItem, item_id)
        assert deleted is not None and deleted.deleted_at is not None
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id == "delete-chain-chat",
            )
        )
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert action is not None and action.consumed_at is not None
        assert action.payload["confirmation_anchor_message_id"] == "delete-C"
        assert action.payload["confirmation_anchor_parent_message_id"] == "delete-B"


@pytest.mark.asyncio
async def test_channel_new_is_blocked_while_delete_effect_applies_then_recovers(db_factory):
    """``/new`` cannot strand an applying delete claim.

    Once the short effect lease is stale, a later confirmation can reclaim
    the durable action and finish the idempotent soft delete in the same
    conversation.
    """

    external_user = f"delete-new-{uuid4().hex}"
    first = ChannelEnvelope(
        channel="telegram",
        account_id="delete-new-account",
        external_user_id=external_user,
        conversation_id="delete-new-chat",
        message_id="delete-new-A",
        text="删除这个条目",
    )
    with db_factory() as db:
        tenant = resolve_or_register(db, first)
        item = ContentItem(
            user_id=tenant.app_user_id,
            platform="youtube",
            platform_id=f"delete-new-{uuid4().hex[:10]}",
            kind="video",
            url="https://youtu.be/delete-new",
            title="delete-new",
            text_source="none",
            state="ready",
        )
        db.add(item)
        db.commit()
        item_id = item.id

    action_services = AgentActionServices(
        submission=IngestSubmissionService(db_factory, lambda _dispatch_id: "task"),
        pending=PendingConfirmationService(db_factory),
        management=KnowledgeItemManagementService(db_factory),
    )

    def model(messages, _info):
        last_request = next(
            message
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
        )
        if any(isinstance(part, ToolReturnPart) for part in last_request.parts):
            return ModelResponse(parts=[TextPart("discarded action prose")])
        prompt = next(
            str(part.content)
            for part in last_request.parts
            if isinstance(part, UserPromptPart)
        )
        if prompt == "删除这个条目":
            tool_name, arguments = "delete_saved_items", {"item_ids": [item_id]}
        elif prompt == "确认" or prompt.startswith("确认删除"):
            tool_name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name,
                    json.dumps(arguments),
                    tool_call_id=f"{tool_name}-{prompt}",
                )
            ]
        )

    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(db_factory, agent, settings)

    requested = await service.handle(first)
    assert requested.error_code == "confirmation_required"
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", requested.text)
    assert code_match is not None
    confirmation_code = code_match.group(1)
    with db_factory() as db:
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id == "delete-new-chat",
            )
        )
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert action is not None
        payload = dict(action.payload)
        payload["effect_state"] = "applying"
        payload["effect_claimed_at"] = datetime.now(UTC).isoformat()
        payload["effect_claim_token"] = "manual-in-flight-claim"
        action.payload = payload
        db.commit()

    blocked = await service.handle(replace(first, message_id="delete-new", text="/new"))
    assert blocked.status == "failed"
    assert blocked.error_code == "delete_in_progress"
    with db_factory() as db:
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id == "delete-new-chat",
            )
        )
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert thread.closed_at is None
        assert action is not None and action.cancelled_at is None
        assert action.payload["effect_state"] == "applying"

        stale_payload = dict(action.payload)
        stale_payload["effect_claimed_at"] = (
            datetime.now(UTC) - timedelta(minutes=2)
        ).isoformat()
        action.payload = stale_payload
        db.commit()

    recovered = await service.handle(
        replace(
            first,
            message_id="delete-new-C",
            text=f"确认删除 {confirmation_code}",
        )
    )
    assert recovered.status == "ok"
    assert recovered.error_code == "items_deleted"
    with db_factory() as db:
        deleted = db.get(ContentItem, item_id)
        assert deleted is not None and deleted.deleted_at is not None
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id == "delete-new-chat",
            )
        )
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert action is not None and action.consumed_at is not None


@pytest.mark.asyncio
async def test_unrelated_question_keeps_live_pending_action_unchanged(db_factory):
    """A durable pending batch must not turn a knowledge question into an action."""

    citation = Citation(
        item_id=901,
        segment_id=902,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )

    class TrackingKnowledge:
        def __init__(self):
            self.calls = []

        def search_segments(self, query, *, limit=10):
            self.calls.append((query, limit))
            return [citation]

    knowledge = TrackingKnowledge()
    published = []
    action_services = AgentActionServices(
        submission=IngestSubmissionService(
            db_factory,
            lambda dispatch_id: published.append(dispatch_id)
            or f"task-{dispatch_id}",
        ),
        pending=PendingConfirmationService(db_factory),
    )
    observed_instructions = []
    bare_url = "https://youtu.be/dQw4w9WgXcQ"
    unrelated_question = "知识库里有什么？"

    def model(messages, info):
        last_request = next(
            message
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
        )
        tool_return = next(
            (
                part
                for part in last_request.parts
                if isinstance(part, ToolReturnPart)
            ),
            None,
        )
        if tool_return is not None:
            if tool_return.tool_name == "request_save_confirmation":
                return ModelResponse(
                    parts=[TextPart("discarded action draft")]
                )
            assert tool_return.tool_name == "search_segments"
            return ModelResponse(
                parts=[TextPart(f"grounded answer [S{citation.segment_id}]")]
            )
        prompt = next(
            str(part.content)
            for part in last_request.parts
            if isinstance(part, UserPromptPart)
        )
        if prompt == bare_url:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "request_save_confirmation",
                        json.dumps({"urls": [bare_url]}),
                        tool_call_id="request-confirmation",
                    )
                ]
        )
        assert prompt == unrelated_question
        # The same FunctionModel is used by the answer-only composer.  Its
        # second request intentionally carries the bounded evidence prompt,
        # not the pending-save state; observe only the retrieval-stage
        # instruction under test.
        if "可信服务器状态：当前 conversation 有 1 个视频等待保存确认。" in info.instructions:
            observed_instructions.append(info.instructions)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": unrelated_question}),
                    tool_call_id="knowledge-search",
                )
            ]
        )

    settings = replace(
        Settings(), agent_timeout_seconds=2
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: knowledge,
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(db_factory, agent, settings)
    external_user = f"pending-knowledge-{uuid4().hex}"

    def message(message_id, text_value):
        return ChannelEnvelope(
            channel="telegram",
            account_id="pending-knowledge-account",
            external_user_id=external_user,
            conversation_id="pending-knowledge-chat",
            message_id=message_id,
            text=text_value,
        )

    pending_answer = await service.handle(message("pending-url", bare_url))
    assert pending_answer.error_code == "save_confirmation_required"
    with db_factory() as db:
        thread = db.scalar(
            select(ConversationThread)
            .join(ChannelIdentity)
            .where(
                ChannelIdentity.external_user_id == external_user,
                ConversationThread.external_conversation_id
                == "pending-knowledge-chat",
                ConversationThread.closed_at.is_(None),
            )
        )
        assert thread is not None
        action = db.scalar(
            select(PendingChannelAction).where(
                PendingChannelAction.thread_id == thread.id
            )
        )
        assert action is not None
        action_id = action.id
        before = (action.consumed_at, action.cancelled_at, action.expires_at)

    answer = await service.handle(message("unrelated-question", unrelated_question))

    assert answer.status == "ok"
    assert answer.citations == [citation]
    assert knowledge.calls == [(unrelated_question, 10)]
    assert len(observed_instructions) == 1
    assert "可信服务器状态：当前 conversation 有 1 个视频等待保存确认。" in observed_instructions[0]
    assert published == []
    with db_factory() as db:
        action = db.get(PendingChannelAction, action_id)
        assert action is not None
        assert action.consumed_at is None
        assert action.cancelled_at is None
        assert (action.consumed_at, action.cancelled_at, action.expires_at) == before


def test_registration_linking_expiry_replay_and_disable_fail_closed(db_factory):
    first = envelope(user=uuid4().hex)
    with db_factory() as db:
        tenant = resolve_or_register(db, first)
        db.commit()
    with db_factory() as db:
        repeated = resolve_or_register(db, first)
        assert repeated == tenant

        token = create_link_token(db, tenant, target_channel="wechat")
        db.commit()
    second = envelope(channel="wechat", user=uuid4().hex)
    with db_factory() as db:
        linked = consume_link_token(db, second, token)
        db.commit()
        assert linked.app_user_id == tenant.app_user_id
    with db_factory() as db:
        with pytest.raises(UsedLinkToken):
            consume_link_token(db, envelope(channel="wechat"), token)

        expired = create_link_token(
            db, tenant, target_channel="wechat", ttl=timedelta(seconds=-1)
        )
        db.commit()
    with db_factory() as db:
        with pytest.raises(ExpiredLinkToken):
            consume_link_token(db, envelope(channel="wechat"), expired)

        wrong = create_link_token(db, tenant, target_channel="slack")
        db.commit()
    with db_factory() as db:
        with pytest.raises(InvalidLinkToken):
            consume_link_token(db, envelope(channel="wechat"), wrong)

        user = db.get(AppUser, tenant.app_user_id)
        user.disabled_at = datetime.now(UTC)
        db.commit()
    with db_factory() as db:
        with pytest.raises(DisabledIdentity):
            resolve_identity(db, first)


def test_link_argument_classification_is_deterministic():
    assert classify_link_argument(" WeChat ") == ("channel", "wechat")
    assert classify_link_argument("telegram") == ("channel", "telegram")
    token = "Ab_" + "c" * 40
    assert classify_link_argument(token) == ("token", token)
    with pytest.raises(InvalidLinkToken):
        classify_link_argument("not a token")


def test_registered_tenants_merge_content_threads_tokens_and_duplicates(db_factory):
    source_envelope = envelope(user=uuid4().hex)
    target_envelope = envelope(channel="wechat", user=uuid4().hex)
    with db_factory() as db:
        source = resolve_or_register(db, source_envelope)
        target = resolve_or_register(db, target_envelope)
        source_thread = get_or_create_thread(db, source, source_envelope)
        target_thread = get_or_create_thread(db, target, target_envelope)
        source_item = ContentItem(
            user_id=source.app_user_id,
            platform="youtube",
            platform_id="duplicate001",
            kind="video",
            url="https://youtu.be/duplicate001",
            saved_at=datetime(2026, 1, 2, tzinfo=UTC),
            why_saved="source reason",
            watch_state="unwatched",
            watch_pos_sec=12,
            state="pending",
        )
        target_item = ContentItem(
            user_id=target.app_user_id,
            platform="youtube",
            platform_id="duplicate001",
            kind="video",
            url="https://youtu.be/duplicate001",
            title="complete title",
            saved_at=datetime(2026, 1, 3, tzinfo=UTC),
            why_saved="target reason",
            watch_state="watched",
            watch_pos_sec=48,
            state="ready",
        )
        target_unique = ContentItem(
            user_id=target.app_user_id,
            platform="youtube",
            platform_id="targetonly1",
            kind="video",
            url="https://youtu.be/targetonly1",
            state="ready",
        )
        db.add_all([source_item, target_item, target_unique])
        db.flush()
        db.add(
            Segment(
                item_id=target_item.id,
                seq=0,
                start_sec=1,
                end_sec=2,
                text="target complete segment",
                embedding=[0.01] * 1536,
                boundary_kind="hard_cut",
            )
        )
        db.add(
            IngestDispatch(
                public_id=uuid4().hex,
                item_id=source_item.id,
                request_key="source-duplicate-request",
                state="enqueued",
            )
        )
        target_token = create_link_token(db, target, target_channel="telegram")
        assert target_token
        link = create_link_token(db, source, target_channel="wechat")
        source_user_id = source.app_user_id
        target_user_id = target.app_user_id
        db.commit()

        linked = consume_link_token(db, target_envelope, link)
        db.commit()

        assert linked.app_user_id == source.app_user_id
        assert db.get(AppUser, target.app_user_id) is None
        assert db.get(ConversationThread, source_thread.id).app_user_id == source.app_user_id
        assert db.get(ConversationThread, target_thread.id).app_user_id == source.app_user_id
        assert {
            identity.app_user_id
            for identity in db.scalars(
                select(ChannelIdentity).where(
                    ChannelIdentity.id.in_([
                        source.channel_identity_id,
                        target.channel_identity_id,
                    ])
                )
            )
        } == {source.app_user_id}
        merged_items = list(
            db.scalars(
                select(ContentItem)
                .where(ContentItem.user_id == source.app_user_id)
                .order_by(ContentItem.platform_id)
            )
        )
        assert [item.platform_id for item in merged_items] == [
            "duplicate001",
            "targetonly1",
        ]
        duplicate = merged_items[0]
        assert duplicate.id == target_item.id
        assert duplicate.saved_at == datetime(2026, 1, 2, tzinfo=UTC)
        assert duplicate.why_saved == "[source] source reason [target] target reason"
        assert (duplicate.watch_state, duplicate.watch_pos_sec) == ("watched", 48)
        assert db.scalar(
            select(func.count(Segment.id)).where(Segment.item_id == duplicate.id)
        ) == 1
        assert db.scalar(
            select(func.count(IngestDispatch.id)).where(
                IngestDispatch.item_id == source_item.id
            )
        ) == 0
        assert db.scalar(
            select(func.count(ChannelLinkToken.id)).where(
                ChannelLinkToken.app_user_id == target_user_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(ChannelLinkToken.id)).where(
                ChannelLinkToken.app_user_id == source_user_id
            )
        ) >= 2


def test_running_target_ingestion_keeps_token_and_ownership_for_retry(db_factory):
    source_envelope = envelope(user=uuid4().hex)
    target_envelope = envelope(channel="wechat", user=uuid4().hex)
    with db_factory() as db:
        source = resolve_or_register(db, source_envelope)
        target = resolve_or_register(db, target_envelope)
        item = ContentItem(
            user_id=target.app_user_id,
            platform="youtube",
            platform_id="runningitem",
            kind="video",
            url="https://youtu.be/runningitem",
            state="fetching",
        )
        db.add(item)
        db.flush()
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key="running-target-request",
            state="running",
        )
        db.add(dispatch)
        link = create_link_token(db, source, target_channel="wechat")
        db.commit()

        with pytest.raises(LinkMergeBusy):
            consume_link_token(db, target_envelope, link)
        token = db.scalar(
            select(ChannelLinkToken).where(
                ChannelLinkToken.app_user_id == source.app_user_id
            )
        )
        assert token.consumed_at is None
        assert resolve_identity(db, target_envelope).app_user_id == target.app_user_id

        dispatch.state = "completed"
        db.commit()
        linked = consume_link_token(db, target_envelope, link)
        db.commit()
        assert linked.app_user_id == source.app_user_id


@pytest.mark.asyncio
async def test_link_commands_merge_registered_target_without_agent_side_effects(
    db_factory,
):
    agent = NoEvidenceAgent()
    settings = replace(Settings(), channel_link_ttl_seconds=600)
    service = ChannelService(db_factory, agent, settings)
    source = envelope(user=uuid4().hex, message="source-start")
    target = envelope(channel="wechat", user=uuid4().hex, message="target-start")
    await service.handle(
        ChannelEnvelope(**{**source.__dict__, "text": "/start"})
    )
    target_before = await service.handle(
        ChannelEnvelope(**{**target.__dict__, "text": "/whoami"})
    )
    generated = await service.handle(
        ChannelEnvelope(
            **{**source.__dict__, "message_id": "source-link", "text": "/link wechat"}
        )
    )
    token = generated.text.splitlines()[0].removeprefix("绑定码：")
    linked = await service.handle(
        ChannelEnvelope(
            **{**target.__dict__, "message_id": "target-link", "text": f"/link {token}"}
        )
    )
    source_after = await service.handle(
        ChannelEnvelope(
            **{**source.__dict__, "message_id": "source-who", "text": "/whoami"}
        )
    )
    target_after = await service.handle(
        ChannelEnvelope(
            **{**target.__dict__, "message_id": "target-who", "text": "/whoami"}
        )
    )

    assert target_before.text != source_after.text
    assert generated.status == linked.status == "ok"
    assert source_after.text == target_after.text
    assert agent.calls == 0
    with db_factory() as db:
        linked_identity_ids = list(
            db.scalars(
                select(ChannelIdentity.id).where(
                    ChannelIdentity.external_user_id.in_(
                        [source.external_user_id, target.external_user_id]
                    )
                )
            )
        )
        assert db.scalar(
            select(func.count(ConversationThread.id)).where(
                ConversationThread.channel_identity_id.in_(linked_identity_ids)
            )
        ) == 0


def test_concurrent_registration_creates_one_user_and_identity():
    try:
        get_engine().connect().close()
    except Exception as exc:
        pytest.skip(f"PostgreSQL integration database unavailable: {type(exc).__name__}")
    factory = get_session_factory()
    shared = envelope(user=f"concurrent-{uuid4().hex}")
    barrier = Barrier(2)

    def register():
        with factory() as db:
            barrier.wait(timeout=5)
            tenant = resolve_or_register(db, shared)
            db.commit()
            return tenant.app_user_id, tenant.channel_identity_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: register(), range(2)))

    assert results[0] == results[1]
    with factory() as db:
        identities = list(
            db.scalars(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == shared.external_user_id
                )
            )
        )
        assert len(identities) == 1
        db.delete(db.get(AppUser, results[0][0]))
        db.commit()


def _add_item(db, user_id, unique_text):
    item = ContentItem(
        user_id=user_id,
        platform="youtube",
        platform_id=uuid4().hex[:11],
        kind="video",
        url="https://youtu.be/example",
        title=f"title-{unique_text}",
        lang="zh",
        state="ready",
    )
    db.add(item)
    db.flush()
    segment = Segment(
        item_id=item.id,
        seq=0,
        start_sec=42,
        end_sec=52,
        text=unique_text,
        embedding=[0.01] * 1536,
        boundary_kind="hard_cut",
    )
    db.add(segment)
    db.flush()
    return item, segment


def test_every_read_service_is_tenant_scoped(db_factory):
    with db_factory() as db:
        tenant_a = resolve_or_register(db, envelope(user=uuid4().hex))
        tenant_b = resolve_or_register(db, envelope(user=uuid4().hex))
        item_a, segment_a = _add_item(db, tenant_a.app_user_id, "甲方专属量子菠萝")
        item_b, segment_b = _add_item(db, tenant_b.app_user_id, "乙方专属月球草莓")
        db.commit()

    embedder = FakeEmbeddingProvider()
    service_a = KnowledgeServices(tenant_a, db_factory, embedder=embedder)
    # Vector ranking may return tenant A's nearest candidate for a query that
    # names tenant B. The security contract is that it can never hydrate B.
    assert {
        value.segment_id for value in service_a.search_segments("乙方专属月球草莓")
    } == {segment_a.id}
    assert service_a.search_segments("甲方专属量子菠萝")[0].segment_id == segment_a.id
    for action in (
        lambda: service_a.get_neighbors(segment_b.id),
        lambda: service_a.get_item(item_b.id),
        lambda: service_a.open_at(segment_b.id),
    ):
        with pytest.raises(KnowledgeNotFound):
            action()
    assert service_a.get_item(item_a.id).item_id == item_a.id
    assert service_a.open_at(segment_a.id).url.endswith("?t=42")
    assert embedder.queries == ["乙方专属月球草莓", "甲方专属量子菠萝"]
    with db_factory() as db:
        vector_hits = vector_search(
            db, [0.01] * 1536, user_id=tenant_a.app_user_id, k=10
        )
    assert {value.segment_id for value in vector_hits} == {segment_a.id}


def test_pgvector_search_diversifies_top_five_items_without_cross_tenant_hydration(db_factory):
    with db_factory() as db:
        tenant_a = resolve_or_register(db, envelope(user=uuid4().hex))
        tenant_b = resolve_or_register(db, envelope(user=uuid4().hex))
        crowded_item, crowded_first = _add_item(
            db, tenant_a.app_user_id, "crowded-vector-evidence-0"
        )
        for sequence in range(1, 8):
            db.add(
                Segment(
                    item_id=crowded_item.id,
                    seq=sequence,
                    start_sec=sequence * 600,
                    end_sec=sequence * 600 + 30,
                    text=f"crowded-vector-evidence-{sequence}",
                    embedding=[0.01] * 1536,
                    boundary_kind="hard_cut",
                )
            )
        other_items = [
            _add_item(db, tenant_a.app_user_id, f"other-vector-evidence-{index}")
            for index in range(1, 6)
        ]
        _, tenant_b_segment = _add_item(
            db, tenant_b.app_user_id, "tenant-b-vector-evidence"
        )
        for item, segment in other_items:
            segment.embedding = [0.01] * 1536
        crowded_first.embedding = [0.01] * 1536
        db.commit()

    service = KnowledgeServices(
        tenant_a,
        db_factory,
        embedder=FakeEmbeddingProvider(),
    )
    citations = service.search_segments("no lexical match", limit=10)

    item_ids = {citation.item_id for citation in citations}
    assert len(item_ids) == 5
    assert crowded_item.id in item_ids
    assert tenant_b_segment.id not in {citation.segment_id for citation in citations}
    # A selected video keeps its distant locations after every selected video
    # has first received a representative candidate.
    assert len([citation for citation in citations if citation.item_id == crowded_item.id]) >= 2


@pytest.mark.asyncio
async def test_agent_tool_uses_real_pgvector_and_hydrates_only_tenant_citation(db_factory):
    """Exercise the live retrieval composition without an external model/provider.

    The deterministic embedder deliberately points closest to tenant B's row.
    The only acceptable response for tenant A is therefore its own hydrated
    citation, proving the Agent tool reaches the tenant-scoped pgvector query.
    """

    with db_factory() as db:
        tenant_a = resolve_or_register(db, envelope(user=uuid4().hex))
        tenant_b = resolve_or_register(db, envelope(user=uuid4().hex))
        item_a, segment_a = _add_item(db, tenant_a.app_user_id, "tenant-a-evidence")
        item_b, segment_b = _add_item(db, tenant_b.app_user_id, "tenant-b-evidence")
        segment_a.embedding = [0.01] * 1536
        segment_b.embedding = [0.99] * 1536
        db.commit()

    class DeterministicQueryEmbedder:
        dimensions = 1536

        def __init__(self):
            self.queries = []

        def embed(self, texts):
            self.queries.extend(texts)
            # If vector_search ever loses the SQL tenant predicate, this is
            # closest to tenant B's segment and the test will expose it.
            return [[0.99] * self.dimensions for _ in texts]

    embedder = DeterministicQueryEmbedder()

    def model(messages, _info):
        has_search_result = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not has_search_result:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        '{"query": "other-tenant-token"}',
                        tool_call_id="search-tenant-filter",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(f"grounded answer [S{segment_a.id}]")])

    runtime = KnowledgeAgent(
        FunctionModel(model),
        replace(Settings(), agent_timeout_seconds=2),
        lambda request: KnowledgeServices(request.tenant, db_factory, embedder=embedder),
    )
    request = AgentRequest(
        question="private question",
        tenant=tenant_a,
        thread_db_id=1,
        thread_public_id="integration-thread",
        message_id="integration-message",
        request_id="integration-request",
    )

    result = await runtime.run(request)

    assert embedder.queries == ["other-tenant-token"]
    assert result.answer.status == "ok"
    assert [citation.segment_id for citation in result.answer.citations] == [segment_a.id]
    assert result.answer.citations[0] == Citation(
        item_id=item_a.id,
        segment_id=segment_a.id,
        title=item_a.title,
        excerpt=segment_a.text,
        url=f"https://youtu.be/{item_a.platform_id}?t=42",
        start_sec=42,
    )
    assert segment_b.id not in {citation.segment_id for citation in result.answer.citations}


class RecordingAgent:
    def __init__(self):
        self.histories = []

    async def run(self, request, *, diagnostics=None):
        self.histories.append(request.history)
        citation = Citation(
            item_id=1,
            segment_id=1,
            title="source",
            excerpt="evidence",
            url="https://example.test?t=1",
            start_sec=1,
        )
        messages = [
            ModelRequest(parts=[UserPromptPart(content=request.question)]),
            ModelResponse(parts=[TextPart(content="answer")]),
        ]
        return AgentExecution(
            AgentAnswer(
                status="ok",
                text="answer",
                citations=[citation],
                thread_id=request.thread_public_id,
            ),
            messages,
        )


class NoEvidenceAgent:
    def __init__(self):
        self.calls = 0

    async def run(self, request, *, diagnostics=None):
        self.calls += 1
        return AgentExecution(
            AgentAnswer(
                status="not_found",
                text="知识库中未找到足够证据。",
                thread_id=request.thread_public_id,
                error_code="no_evidence",
            ),
            [],
        )


@pytest.mark.asyncio
async def test_context_recovers_after_service_restart_and_replay_is_idempotent(db_factory):
    settings = replace(Settings(), context_max_turns=4, context_token_budget=1000)
    agent = RecordingAgent()
    first_service = ChannelService(db_factory, agent, settings)
    external = uuid4().hex
    first = envelope(user=external, message="m1")
    await first_service.handle(first)

    restarted_service = ChannelService(db_factory, agent, settings)
    second = envelope(user=external, message="m2")
    await restarted_service.handle(second)
    await restarted_service.handle(second)

    assert agent.histories[0] == ()
    assert len(agent.histories[1]) == 2
    assert len(agent.histories) == 2


@pytest.mark.asyncio
async def test_no_evidence_replay_keeps_its_distinct_status_and_code(db_factory):
    settings = replace(Settings(), context_max_turns=4, context_token_budget=1000)
    agent = NoEvidenceAgent()
    service = ChannelService(db_factory, agent, settings)
    original = envelope(user=uuid4().hex, message="no-evidence-message")

    first = await service.handle(original)
    replay = await service.handle(original)

    assert first.status == replay.status == "not_found"
    assert first.error_code == replay.error_code == "no_evidence"
    assert agent.calls == 1


def test_context_window_and_channels_do_not_mix(db_factory):
    with db_factory() as db:
        tg_envelope = envelope(user=uuid4().hex, conversation="same-label")
        wx_envelope = envelope(channel="wechat", user=uuid4().hex, conversation="same-label")
        tg_tenant = resolve_or_register(db, tg_envelope)
        link = create_link_token(db, tg_tenant, target_channel="wechat")
        db.flush()
        wx_tenant = consume_link_token(db, wx_envelope, link)
        assert wx_tenant.app_user_id == tg_tenant.app_user_id
        tg_thread = get_or_create_thread(db, tg_tenant, tg_envelope)
        wx_thread = get_or_create_thread(db, wx_tenant, wx_envelope)
        for index in range(3):
            current = envelope(user=tg_envelope.external_user_id, message=f"m{index}")
            save_completed_turn(
                db,
                thread=tg_thread,
                envelope=current,
                assistant_text=f"a{index}",
                sources=[],
                model_messages=[
                    ModelRequest(parts=[UserPromptPart(content=f"q{index}")]),
                    ModelResponse(parts=[TextPart(content=f"a{index}")]),
                ],
            )
        db.commit()
    with db_factory() as db:
        history = load_message_history(db, tg_thread.id, max_turns=2, max_tokens=1000)
        token_limited = load_message_history(
            db, tg_thread.id, max_turns=10, max_tokens=1
        )
        other = load_message_history(db, wx_thread.id, max_turns=10, max_tokens=1000)
    assert len(history) == 4
    assert token_limited == []
    assert other == []


def test_same_external_conversation_id_is_isolated_by_tenant_identity(db_factory):
    """The same client conversation label must not join two Web tenants."""

    conversation_id = f"shared-web-conversation-{uuid4().hex}"
    message_a = envelope(
        channel="web",
        user=f"web-a-{uuid4().hex}",
        message="message-a",
        conversation=conversation_id,
    )
    message_b = envelope(
        channel="web",
        user=f"web-b-{uuid4().hex}",
        message="message-b",
        conversation=conversation_id,
    )
    with db_factory() as db:
        tenant_a = resolve_or_register(db, message_a)
        tenant_b = resolve_or_register(db, message_b)
        thread_a = get_or_create_thread(db, tenant_a, message_a)
        thread_b = get_or_create_thread(db, tenant_b, message_b)
        save_completed_turn(
            db,
            thread=thread_a,
            envelope=message_a,
            assistant_text="answer-a",
            sources=[],
            model_messages=[
                ModelRequest(parts=[UserPromptPart(content="message-a")]),
                ModelResponse(parts=[TextPart(content="answer-a")]),
            ],
        )
        save_completed_turn(
            db,
            thread=thread_b,
            envelope=message_b,
            assistant_text="answer-b",
            sources=[],
            model_messages=[
                ModelRequest(parts=[UserPromptPart(content="message-b")]),
                ModelResponse(parts=[TextPart(content="answer-b")]),
            ],
        )
        db.commit()

    assert tenant_a.app_user_id != tenant_b.app_user_id
    assert tenant_a.channel_identity_id != tenant_b.channel_identity_id
    assert thread_a.id != thread_b.id
    assert thread_a.public_id != thread_b.public_id
    assert thread_a.external_conversation_id == thread_b.external_conversation_id == conversation_id
    assert thread_a.app_user_id == tenant_a.app_user_id
    assert thread_b.app_user_id == tenant_b.app_user_id

    with db_factory() as db:
        history_a = load_message_history(
            db, thread_a.id, max_turns=10, max_tokens=1_000
        )
        history_b = load_message_history(
            db, thread_b.id, max_turns=10, max_tokens=1_000
        )

    assert ModelMessagesTypeAdapter.dump_python(
        history_a, mode="json"
    ) != ModelMessagesTypeAdapter.dump_python(history_b, mode="json")


@pytest.mark.asyncio
async def test_new_command_closes_old_context_and_disabled_user_skips_agent(db_factory):
    settings = replace(Settings(), context_max_turns=4, context_token_budget=1000)
    agent = RecordingAgent()
    service = ChannelService(db_factory, agent, settings)
    external = uuid4().hex
    first = envelope(user=external, message="before")
    await service.handle(first)

    reset = envelope(user=external, message="reset")
    reset = ChannelEnvelope(
        reset.channel,
        reset.account_id,
        reset.external_user_id,
        reset.conversation_id,
        reset.message_id,
        "/new",
    )
    reset_answer = await service.handle(reset)
    assert reset_answer.status == "ok"

    after = envelope(user=external, message="after")
    await service.handle(after)
    assert agent.histories[-1] == ()

    with db_factory() as db:
        tenant = resolve_identity(db, after)
        db.get(AppUser, tenant.app_user_id).disabled_at = datetime.now(UTC)
        db.commit()
    failed = await service.handle(envelope(user=external, message="disabled"))
    assert failed.status == "failed"
    assert failed.error_code == "identity_error"
    assert len(agent.histories) == 2
