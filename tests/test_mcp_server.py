from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.agent.types import AgentAnswer, Citation
from app.agent.management import BatchItemOperationResult, ItemOperationResult
from app.agent.runtime import KnowledgeAgent
from app.channels.service import ChannelService
from app.channels.types import ChannelEnvelope
from app.channels.pending_actions import ConfirmationResult
from app.channels.types import TenantContext
from app.config import Settings
from app.mcp_grants import (
    InsufficientMcpScope,
    McpGrantService,
    McpGrantError,
    McpGrantMetadata,
    ResolvedMcpGrant,
)
from app.mcp_server import (
    McpToolFacade,
    _mcp_transport_security,
    allowed_tool_names,
    create_mcp_server,
    create_streamable_http_app,
    extract_authentication,
    redact_request_uri,
)
from app.mcp_readiness import (
    _inspect_worker,
    assess_mcp_mutation_readiness,
    probe_mcp_worker,
)
from app.models import (
    AppUser,
    ChannelIdentity,
    ConversationThread,
    ConversationTurn,
    McpAccessGrant,
    PendingChannelAction,
)


def test_mcp_lazy_submission_uses_every_configured_ingest_quota(monkeypatch):
    settings = Settings(
        ingest_max_active_per_user=2,
        ingest_daily_new_item_limit=3,
        ingest_max_items_per_user=4,
        ingest_max_active_global=5,
        ingest_daily_new_item_limit_global=6,
        ingest_daily_dispatch_limit_per_user=7,
        ingest_daily_dispatch_limit_global=8,
    )
    monkeypatch.setattr("app.bootstrap.build_channel_service", lambda _settings: object())
    facade = McpToolFacade(
        settings=settings,
        session_factory=lambda: None,
        management=object(),
        pending=object(),
        publisher=lambda _dispatch_id: "task",
    )

    facade._ensure_services()

    policy = facade.submission.quota_policy
    assert policy.max_active_per_tenant == 2
    assert policy.daily_new_item_limit == 3
    assert policy.max_items_per_tenant == 4
    assert policy.max_active_global == 5
    assert policy.daily_new_item_limit_global == 6
    assert policy.daily_dispatch_limit_per_tenant == 7
    assert policy.daily_dispatch_limit_global == 8


def _sqlite_grants():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (AppUser.__table__, ChannelIdentity.__table__, McpAccessGrant.__table__):
        table.create(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as db:
        db.add_all([AppUser(id=1), AppUser(id=2)])
        db.commit()
    return engine, factory


def _sqlite_channel_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        for statement in (
            """
            CREATE TABLE app_user (
                id INTEGER PRIMARY KEY,
                created_at DATETIME,
                disabled_at DATETIME
            );
            """,
            """
            CREATE TABLE channel_identity (
                id INTEGER PRIMARY KEY,
                app_user_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                created_at DATETIME,
                disabled_at DATETIME
            );
            """,
            """
            CREATE TABLE conversation_thread (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL,
                app_user_id INTEGER NOT NULL,
                channel_identity_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_conversation_id TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                closed_at DATETIME
            );
            """,
            """
            CREATE TABLE conversation_turn (
                id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                sources TEXT NOT NULL,
                model_messages TEXT NOT NULL,
                answer_status TEXT NOT NULL DEFAULT 'legacy',
                error_code TEXT,
                action_results TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE pending_channel_action (
                id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                consumed_at DATETIME,
                consumed_message_id TEXT,
                cancelled_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
        ):
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO app_user(id) VALUES (1)"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO channel_identity(id, app_user_id, channel, account_id, external_user_id)
            VALUES (2, 1, 'mcp', 'mcp', 'grant-principal')
            """
        )

    def factory():
        return Session(engine, expire_on_commit=False)

    return engine, factory


def test_grants_are_hash_only_multi_user_and_revocable():
    engine, factory = _sqlite_grants()
    try:
        service = McpGrantService(factory)
        first = service.issue(1, scope="read")
        second = service.issue(2, scope="full")
        assert len(first.raw_token.removeprefix("mcp_")) >= 43
        with factory() as db:
            row = db.scalar(
                select(McpAccessGrant).where(
                    McpAccessGrant.grant_id == first.metadata.grant_id
                )
            )
            assert row is not None
            assert first.raw_token not in row.token_hash
            assert len(row.token_hash) == 64
        assert service.resolve(first.raw_token).tenant.app_user_id == 1
        with pytest.raises(InsufficientMcpScope):
            service.resolve(first.raw_token, required_scope="full")
        assert service.resolve(second.raw_token, required_scope="full").tenant.app_user_id == 2
        rotated = service.rotate(first.grant_id)
        with pytest.raises(McpGrantError):
            service.resolve(first.raw_token)
        assert service.resolve(rotated.raw_token).grant_id == first.grant_id
        service.revoke(first.grant_id)
        with pytest.raises(McpGrantError):
            service.resolve(rotated.raw_token)
    finally:
        engine.dispose()


def test_grant_expiry_is_optional_and_fail_closed():
    engine, factory = _sqlite_grants()
    try:
        service = McpGrantService(factory)
        issued = service.issue(
            1,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        assert service.resolve(issued.raw_token)
        with pytest.raises(McpGrantError):
            service.resolve(issued.raw_token, now=datetime.now(UTC) + timedelta(minutes=2))
    finally:
        engine.dispose()


def test_grant_lifecycle_scopes_pagination_and_disabled_principals():
    engine, factory = _sqlite_grants()
    try:
        service = McpGrantService(factory)
        grants = [
            service.issue(1, scope="read", label="read one"),
            service.issue(1, scope="full", label="full one"),
            service.issue(1, scope="read", label="read two"),
            service.issue(2, scope="full", label="other user"),
        ]
        assert {item.metadata.scope for item in grants[:3]} == {"read", "full"}
        assert len(service.list(app_user_id=1, limit=2, offset=0)) == 2
        assert len(service.list(app_user_id=1, limit=2, offset=2)) == 1
        with pytest.raises(McpGrantError, match="invalid_limit"):
            service.list(limit=0)
        with pytest.raises(McpGrantError, match="invalid_offset"):
            service.list(offset=-1)

        disabled = service.disable(grants[0].grant_id)
        assert disabled.disabled_at is not None
        with pytest.raises(McpGrantError):
            service.resolve(grants[0].raw_token)

        with factory() as db:
            identity = db.scalar(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == grants[1].grant_id
                )
            )
            assert identity is not None
            identity.disabled_at = datetime.now(UTC)
            db.commit()
        with pytest.raises(McpGrantError):
            service.resolve(grants[1].raw_token)

        with factory() as db:
            user = db.get(AppUser, 2)
            assert user is not None
            user.disabled_at = datetime.now(UTC)
            db.commit()
        with pytest.raises(McpGrantError):
            service.resolve(grants[3].raw_token)

        # The grant is durable across service objects; only its raw token is
        # intentionally unavailable to metadata/list callers.
        restarted = McpGrantService(factory)
        assert restarted.get(grants[2].grant_id).scope == "read"
        assert "raw_token" not in restarted.get(grants[2].grant_id).model_dump()
    finally:
        engine.dispose()


def test_auth_header_preferred_path_mode_is_explicit_and_query_tokens_rejected():
    auth = extract_authentication(
        {"Authorization": "Bearer opaque"}, path="/mcp", query_string=""
    )
    assert auth.token == "opaque" and not auth.from_url_path
    with pytest.raises(Exception):
        extract_authentication({}, path="/mcp/c/opaque", url_token_mode=False)
    auth = extract_authentication(
        {}, path="/mcp/c/opaque", url_token_mode=True, scheme="https"
    )
    assert auth.from_url_path and auth.canonical_path == "/mcp"
    with pytest.raises(Exception):
        extract_authentication({}, path="/mcp", query_string="token=opaque")
    assert redact_request_uri("/mcp/c/opaque?x=1") == "/mcp"


class _FakeChannelService:
    def __init__(self):
        self.envelopes = []
        self.error = None

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        if self.error is not None:
            raise RuntimeError(self.error)
        return AgentAnswer(
            status="ok",
            text="grounded answer",
            citations=[
                Citation(
                    item_id=3,
                    segment_id=8,
                    title="source",
                    excerpt="evidence",
                    url="https://example.test/source",
                    start_sec=4,
                )
            ],
            thread_id="thread-public",
        )


class _ControlledKnowledgeServices:
    def __init__(self, citation: Citation):
        self.citation = citation
        self.calls = []

    def search_segments(self, query, *, limit=10):
        self.calls.append(("search_segments", query))
        return [self.citation]

    def get_neighbors(self, segment_id, *, radius=1):
        self.calls.append(("get_neighbors", segment_id))
        return [self.citation]

    def get_item(self, item_id):
        self.calls.append(("get_item", item_id))
        return None

    def open_at(self, segment_id):
        self.calls.append(("open_at", segment_id))
        return self.citation


def _resolved(scope="read"):
    metadata = McpGrantMetadata(
        grant_id="grant-principal",
        app_user_id=1,
        scope=scope,
        expires_at=None,
        revoked_at=None,
        disabled_at=None,
        created_at=None,
        updated_at=None,
        rotated_at=None,
        last_used_at=None,
        label=None,
        created_by=None,
    )
    tenant = TenantContext(1, 2, "mcp", "mcp", "grant-principal")
    return ResolvedMcpGrant(metadata, tenant)


@pytest.mark.asyncio
async def test_ask_facade_uses_channel_service_and_rejects_commands():
    channel = _FakeChannelService()
    facade = McpToolFacade(channel_service=channel, grant=_resolved())
    result = await facade.ask_notebook_agent("What is in my notes?", "conversation-a")
    assert result.status == "ok"
    assert result.answer == "grounded answer"
    assert result.citations[0].start_sec == 4
    assert channel.envelopes[0].channel == "mcp"
    assert channel.envelopes[0].conversation_id == "conversation-a"
    assert channel.envelopes[0].request_id
    command = await facade.ask_notebook_agent("/start")
    assert command.status == "failed"
    assert command.error_code == "slash_command_not_allowed"
    assert len(channel.envelopes) == 1


@pytest.mark.asyncio
async def test_mcp_natural_question_reaches_real_channel_and_knowledge_agent():
    engine, factory = _sqlite_channel_factory()
    try:
        import app.channels.service as channel_module

        tenant = TenantContext(1, 2, "mcp", "mcp", "grant-principal")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            channel_module,
            "resolve_or_register",
            lambda _db, _envelope: tenant,
        )
        citation = Citation(
            item_id=3,
            segment_id=8,
            title="source",
            excerpt="grounded evidence",
            url="https://example.test/source",
            start_sec=4,
        )
        services = _ControlledKnowledgeServices(citation)
        planner_calls = []

        def planner(_messages, info):
            planner_calls.append(info)
            names = {tool.name for tool in info.function_tools}
            if "search_segments" in names and not services.calls:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "search_segments",
                            json.dumps({"query": "What is in my notes?"}),
                            tool_call_id=f"search-{len(planner_calls)}",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("planner complete")])

        settings = replace(
            Settings(),
            agent_timeout_seconds=2,
        )
        agent = KnowledgeAgent(
            FunctionModel(planner),
            settings,
            lambda _request: services,
            composer_model=TestModel(
                custom_output_text=json.dumps(
                    {"sections": [{"text": "grounded", "citation_ids": [8]}]}
                )
            ),
        )
        channel = ChannelService(factory, agent, settings)
        facade = McpToolFacade(channel_service=channel, grant=_resolved())
        server = create_mcp_server(scope="read", facade=facade)
        from mcp.client.session import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams

        async with create_client_server_memory_streams() as (client_streams, server_streams):
            server_task = asyncio.create_task(
                server._lowlevel_server.run(
                    *server_streams,
                    server._lowlevel_server.create_initialization_options(),
                )
            )
            async with ClientSession(*client_streams) as client:
                await client.initialize()
                natural = await client.call_tool(
                    "ask_notebook_agent",
                    {
                        "question": "What is in my notes?",
                        "conversation_id": "mcp-conversation",
                    },
                )
                assert natural.is_error is False
                assert natural.structured_content["status"] == "ok"
                assert natural.structured_content["citations"][0]["segment_id"] == 8
                assert services.calls == [("search_segments", "What is in my notes?")]
                assert planner_calls
                calls_before_command = len(planner_calls)

                command = await client.call_tool(
                    "ask_notebook_agent",
                    {"question": "/start", "conversation_id": "mcp-conversation"},
                )
                assert command.structured_content["error_code"] == "slash_command_not_allowed"
                assert len(planner_calls) == calls_before_command

                invalid = await client.call_tool(
                    "ask_notebook_agent", {"conversation_id": "mcp-conversation"}
                )
                assert invalid.is_error is True
                assert invalid.structured_content is None
                assert len(planner_calls) == calls_before_command
            await server_task
    finally:
        monkeypatch.undo()
        engine.dispose()


@pytest.mark.asyncio
async def test_delete_request_never_returns_code_when_anchor_persistence_fails():
    class Pending:
        def __init__(self):
            self.cancelled = []

        def request_delete(self, *args, **kwargs):
            return ConfirmationResult(
                "confirmation_required",
                item_ids=(7,),
                confirmation_code="SECRET-CODE",
            )

        def cancel_delete(self, tenant, thread_id):
            self.cancelled.append((tenant, thread_id))
            return ConfirmationResult("cancelled")

    pending = Pending()
    facade = McpToolFacade(
        channel_service=object(),
        grant=_resolved("full"),
        pending=pending,
        management=object(),
    )
    facade._management_thread_safe = lambda *args, **kwargs: ((42, "thread", None), None)
    facade._record_management_turn = lambda *args, **kwargs: False

    output = await facade.request_delete_saved_items([7], "conversation")
    assert output.status == "failed"
    assert output.error_code == "management_unavailable"
    assert output.confirmation_code is None
    assert pending.cancelled == [(_resolved("full").tenant, 42)]


@pytest.mark.asyncio
async def test_mcp_fresh_delete_request_then_confirm_uses_same_conversation_anchor():
    class Management:
        def request_delete_targets(self, _tenant, item_ids):
            return tuple(item_ids)

        def soft_delete(self, _tenant, item_ids, **_kwargs):
            return BatchItemOperationResult(
                results=[
                    ItemOperationResult(item_id=item_id, status="deleted")
                    for item_id in item_ids
                ]
            )

    engine, factory = _sqlite_channel_factory()
    try:
        from app.channels.pending_actions import PendingConfirmationService

        facade = McpToolFacade(
            channel_service=object(),
            grant=_resolved("full"),
            session_factory=factory,
            pending=PendingConfirmationService(factory),
            management=Management(),
        )

        # This is a genuinely fresh conversation: the thread helper returns
        # latest=None before the pending action and its durable anchor exist.
        requested = await facade.request_delete_saved_items([7], "fresh-mcp-chat")
        assert requested.status == "confirmation_required"
        assert requested.confirmation_code

        with factory() as db:
            thread = db.scalar(
                select(ConversationThread).where(
                    ConversationThread.external_conversation_id == "fresh-mcp-chat"
                )
            )
            assert thread is not None
            action = db.scalar(
                select(PendingChannelAction).where(
                    PendingChannelAction.thread_id == thread.id,
                    PendingChannelAction.kind == "delete_saved_items",
                )
            )
            assert action is not None
            assert action.payload["confirmation_anchor_parent_message_id"] is None
            assert action.payload["confirmation_anchor_message_id"] == action.payload["request_message_id"]
            markers = list(
                db.scalars(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.thread_id == thread.id,
                        ConversationTurn.answer_status == "mcp_management",
                    )
                    .order_by(ConversationTurn.id)
                )
            )
            assert len(markers) == 1
            assert markers[0].message_id == action.payload["request_message_id"]

        confirmed = await facade.confirm_item_deletion(
            requested.confirmation_code, "fresh-mcp-chat"
        )
        assert confirmed.status == "confirmed"
        assert [row.model_dump() for row in confirmed.results] == [
            {
                "item_id": 7,
                "status": "deleted",
                "safe_error_code": None,
                "result_id": None,
                "input_index": None,
                "state": None,
                "item_ids": None,
            }
        ]

        with factory() as db:
            thread = db.scalar(
                select(ConversationThread).where(
                    ConversationThread.external_conversation_id == "fresh-mcp-chat"
                )
            )
            assert thread is not None
            action = db.scalar(
                select(PendingChannelAction).where(
                    PendingChannelAction.thread_id == thread.id,
                    PendingChannelAction.kind == "delete_saved_items",
                )
            )
            assert action is not None
            assert action.consumed_at is not None
            assert action.consumed_message_id
            assert action.payload["effect_state"] == "applied"
            markers = list(
                db.scalars(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.thread_id == thread.id,
                        ConversationTurn.answer_status == "mcp_management",
                    )
                    .order_by(ConversationTurn.id)
                )
            )
            assert len(markers) == 2
            assert markers[1].message_id == action.consumed_message_id
    finally:
        engine.dispose()


def test_mcp_management_markers_do_not_evict_real_history_before_turn_cap():
    from app.channels.conversations import (
        get_or_create_thread,
        load_message_history,
        save_completed_turn,
    )
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    engine, factory = _sqlite_channel_factory()
    try:
        tenant = TenantContext(1, 2, "mcp", "mcp", "grant-principal")
        base = ChannelEnvelope(
            channel="mcp",
            account_id="mcp",
            external_user_id="grant-principal",
            conversation_id="history-chat",
            message_id="seed",
            text="seed",
        )
        with factory() as db:
            thread = get_or_create_thread(db, tenant, base)
            for index in range(2):
                save_completed_turn(
                    db,
                    thread=thread,
                    envelope=ChannelEnvelope(
                        **{
                            **base.__dict__,
                            "message_id": f"real-{index}",
                            "text": f"real question {index}",
                        }
                    ),
                    assistant_text=f"real answer {index}",
                    sources=(),
                    model_messages=[
                        ModelRequest(parts=[UserPromptPart(f"real question {index}")]),
                        ModelResponse(parts=[TextPart(f"real answer {index}")]),
                    ],
                    answer_status="ok",
                )
            for index in range(12):
                save_completed_turn(
                    db,
                    thread=thread,
                    envelope=ChannelEnvelope(
                        **{
                            **base.__dict__,
                            "message_id": f"marker-{index}",
                            "text": "mcp management action",
                        }
                    ),
                    assistant_text="mcp management action",
                    sources=(),
                    model_messages=(),
                    answer_status="mcp_management",
                )
            db.commit()
            history = load_message_history(
                db, thread.id, max_turns=2, max_tokens=1000
            )
        text = " ".join(
            str(part.content)
            for message in history
            for part in message.parts
            if hasattr(part, "content")
        )
        assert "real question 0" in text
        assert "real question 1" in text
        assert "mcp management action" not in text
    finally:
        engine.dispose()


def test_scope_controls_discovery_and_server_is_lazy():
    read = create_mcp_server(scope="read", facade=McpToolFacade(grant=_resolved()))
    full = create_mcp_server(scope="full", facade=McpToolFacade(grant=_resolved("full")))
    assert read.allowed_tool_names == allowed_tool_names("read")
    assert full.allowed_tool_names == allowed_tool_names("full")
    assert set(read.allowed_tool_names) == {
        "ask_notebook_agent", "list_saved_items", "get_saved_item"
    }
    assert len(full.allowed_tool_names) == 10


def test_mcp_path_rejects_root_and_preserves_custom_non_root_path():
    with pytest.raises(ValueError, match="MCP_PATH"):
        Settings(mcp_path="/")
    assert Settings(mcp_path="/private/mcp").mcp_path == "/private/mcp"
    auth = extract_authentication(
        {},
        path="/private/mcp/c/opaque",
        canonical_path="/private/mcp",
        url_token_mode=True,
        scheme="https",
    )
    assert auth.canonical_path == "/private/mcp"
    assert redact_request_uri(
        "/private/mcp/c/opaque?x=1", canonical_path="/private/mcp"
    ) == "/private/mcp"


def test_mutation_readiness_is_bounded_and_full_profile_withholds_mutations():
    settings = Settings()
    result = assess_mcp_mutation_readiness(
        settings,
        database_probe=lambda _: True,
        broker_probe=lambda _: False,
        object_store_probe=lambda _: True,
        maintenance_probe=lambda _: True,
        worker_probe=lambda _: True,
    )
    assert not result.ready
    assert result.checks == {
        "database": True,
        "broker": False,
        "object_store": True,
        "maintenance": True,
        "worker": True,
    }
    assert result.error_code == "broker_unavailable"

    facade = McpToolFacade(
        grant=_resolved("full"),
        mutation_ready=False,
        mutation_error_code=result.error_code,
    )
    server = create_mcp_server(scope="full", facade=facade)
    assert set(server.allowed_tool_names) == {
        "ask_notebook_agent", "list_saved_items", "get_saved_item",
    }
    output = asyncio.run(facade.update_saved_item(1, "reason"))
    assert output.status == "failed"
    assert output.error_code == "broker_unavailable"


def test_worker_readiness_present_absent_error_and_timeout_are_bounded():
    import app.mcp_readiness as readiness

    assert readiness._WORKER_INSPECT_TIMEOUT_SECONDS >= 5
    assert (
        readiness._WORKER_TOTAL_TIMEOUT_SECONDS
        > readiness._WORKER_INSPECT_TIMEOUT_SECONDS * 2
    )
    settings = Settings()
    assert probe_mcp_worker(settings, inspector=lambda _: True, timeout_seconds=0.1)
    assert not probe_mcp_worker(settings, inspector=lambda _: False, timeout_seconds=0.1)

    def broken(_settings):
        raise RuntimeError("private broker details")

    assert not probe_mcp_worker(settings, inspector=broken, timeout_seconds=0.1)

    def hangs(_settings):
        time.sleep(0.2)
        return True

    started = time.monotonic()
    assert not probe_mcp_worker(settings, inspector=hangs, timeout_seconds=0.01)
    assert time.monotonic() - started < 0.15


def test_celery_worker_inspection_requires_pong_and_mutation_queues(monkeypatch):
    import app.mcp_readiness as readiness
    import app.ingest.tasks as tasks

    class Inspector:
        def __init__(self, pongs, queues, error=None):
            self.pongs = pongs
            self.queues = queues
            self.error = error

        def ping(self):
            if self.error is not None:
                raise self.error
            return self.pongs

        def active_queues(self):
            return self.queues

    class Control:
        def __init__(self, inspector):
            self.inspector = inspector

        def inspect(self, *, timeout):
            assert timeout == readiness._WORKER_INSPECT_TIMEOUT_SECONDS
            return self.inspector

    class App:
        def __init__(self, inspector):
            self.control = Control(inspector)

    present = Inspector(
        {"worker-a": {"ok": "pong"}},
        {"worker-a": [{"name": "ingest"}, {"name": "maintenance"}]},
    )
    monkeypatch.setattr(tasks, "celery_app", App(present))
    assert _inspect_worker(Settings())

    absent = Inspector(
        {"worker-a": {"ok": "pong"}}, {"worker-a": [{"name": "ingest"}]}
    )
    monkeypatch.setattr(tasks, "celery_app", App(absent))
    assert not _inspect_worker(Settings())

    broken = Inspector({}, {}, error=RuntimeError("private broker error"))
    monkeypatch.setattr(tasks, "celery_app", App(broken))
    with pytest.raises(RuntimeError):
        _inspect_worker(Settings())


def test_default_readiness_fails_closed_when_worker_is_absent(monkeypatch):
    import app.mcp_readiness as readiness_module

    monkeypatch.setattr(readiness_module, "_inspect_worker", lambda _settings: False)
    result = assess_mcp_mutation_readiness(
        Settings(),
        database_probe=lambda _: True,
        broker_probe=lambda _: True,
        object_store_probe=lambda _: True,
        maintenance_probe=lambda _: True,
    )
    assert not result.ready
    assert result.checks["worker"] is False
    assert result.error_code == "worker_unavailable"


@pytest.mark.asyncio
async def test_official_sdk_in_memory_protocol_profiles_and_schema_bounds():
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    async def inspect_server(server):
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            task = asyncio.create_task(
                server._lowlevel_server.run(
                    *server_streams,
                    server._lowlevel_server.create_initialization_options(),
                )
            )
            async with ClientSession(*client_streams) as client:
                await client.initialize()
                tools = await client.list_tools()
                result = await client.call_tool(
                    "ask_notebook_agent", {"question": "/start"}
                )
            await task
            return tools, result

    read_tools, read_result = await inspect_server(
        create_mcp_server(scope="read", facade=McpToolFacade(grant=_resolved()))
    )
    full_tools, _ = await inspect_server(
        create_mcp_server(scope="full", facade=McpToolFacade(grant=_resolved("full")))
    )
    assert len(read_tools.tools) == 3
    assert len(full_tools.tools) == 10
    assert read_result.structured_content["error_code"] == "slash_command_not_allowed"
    ask_schema = next(
        tool for tool in read_tools.tools if tool.name == "ask_notebook_agent"
    ).output_schema
    assert ask_schema["properties"]["citations"]["maxItems"] == 10


def test_streamable_http_auth_scope_path_mode_and_provider_safe_projection():
    from starlette.testclient import TestClient

    engine, factory = _sqlite_grants()
    try:
        grants = McpGrantService(factory)
        read_grant = grants.issue(1, scope="read")
        full_grant = grants.issue(1, scope="full")
        settings = Settings(
            mcp_host="127.0.0.1",
            mcp_port=8000,
            mcp_path="/mcp",
            mcp_url_token_mode=True,
        )
        channel = _FakeChannelService()
        server = create_mcp_server(
            scope="full",
            facade=McpToolFacade(
                grant_service=grants,
                channel_service=channel,
                settings=settings,
                mutation_ready=True,
            ),
        )
        app = create_streamable_http_app(
            server=server, grant_service=grants, settings=settings
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }

        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            assert client.post("/mcp", json=initialize).status_code == 401
            assert client.post(
                "/mcp", json=initialize, headers={"Authorization": "Bearer"}
            ).status_code == 401
            assert client.post(
                f"/mcp?token={read_grant.raw_token}", json=initialize
            ).status_code == 401

            read_headers = {"Authorization": f"Bearer {read_grant.raw_token}"}
            full_headers = {"Authorization": f"Bearer {full_grant.raw_token}"}
            assert client.post("/mcp", json=initialize, headers=read_headers).status_code == 200
            read_list = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=read_headers,
            )
            assert read_list.status_code == 200
            assert len(read_list.json()["result"]["tools"]) == 3

            assert client.post("/mcp", json=initialize, headers=full_headers).status_code == 200
            full_list = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                headers=full_headers,
            )
            assert full_list.status_code == 200
            assert len(full_list.json()["result"]["tools"]) == 10
            call = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_notebook_agent",
                        "arguments": {"question": "What is in my notes?"},
                    },
                },
                headers=full_headers,
            )
            assert call.status_code == 200
            assert call.json()["result"]["structuredContent"]["answer"] == "grounded answer"
            channel.error = "provider secret payload"
            provider_failure = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_notebook_agent",
                        "arguments": {"question": "What is in my notes?"},
                    },
                },
                headers=full_headers,
            )
            assert provider_failure.status_code == 200
            failure_body = provider_failure.json()["result"]["structuredContent"]
            assert failure_body["status"] == "failed"
            assert "provider secret payload" not in provider_failure.text
            envelopes_before_invalid = len(channel.envelopes)
            invalid = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "ask_notebook_agent", "arguments": {}},
                },
                headers=full_headers,
            )
            assert invalid.status_code in {200, 400}
            if invalid.status_code == 200:
                assert invalid.json()["result"]["isError"] is True
            assert len(channel.envelopes) == envelopes_before_invalid

            # Header credentials are authoritative on the canonical route;
            # revocation takes effect on the next request.
            grants.revoke(full_grant.grant_id)
            assert client.post("/mcp", json=initialize, headers=full_headers).status_code == 401

        # URL-token compatibility is explicitly HTTPS-only and rewrites to
        # the canonical path internally.
        path_server = create_mcp_server(
            scope="full",
            facade=server.mcp_facade,
        )
        path_app = create_streamable_http_app(
            server=path_server, grant_service=grants, settings=settings
        )
        with TestClient(path_app, base_url="http://127.0.0.1:8000") as client:
            assert client.post(
                f"/mcp/c/{read_grant.raw_token}", json=initialize
            ).status_code == 401
        https_app = create_streamable_http_app(
            server=create_mcp_server(scope="full", facade=server.mcp_facade),
            grant_service=grants,
            settings=settings,
        )
        with TestClient(https_app, base_url="https://127.0.0.1:8000") as client:
            path_init = client.post(
                f"/mcp/c/{read_grant.raw_token}", json=initialize
            )
            assert path_init.status_code == 200
            path_list = client.post(
                f"/mcp/c/{read_grant.raw_token}",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            assert path_list.status_code == 200
            assert len(path_list.json()["result"]["tools"]) == 3
    finally:
        engine.dispose()


def test_streamable_http_keeps_rebinding_protection_and_allows_validated_public_origin():
    from starlette.testclient import TestClient

    engine, factory = _sqlite_grants()
    try:
        grants = McpGrantService(factory)
        grant = grants.issue(1, scope="full")
        settings = Settings(
            notebook_agent_env="development",
            web_auth_enabled=True,
            web_public_origin="https://notebookai.deequoique.tech",
            web_auth_secret="x" * 32,
            mcp_host="127.0.0.1",
            mcp_port=8000,
            mcp_path="/mcp",
        )
        app = create_streamable_http_app(
            server=create_mcp_server(
                scope="full",
                facade=McpToolFacade(
                    grant_service=grants,
                    channel_service=_FakeChannelService(),
                    settings=settings,
                    mutation_ready=True,
                ),
            ),
            grant_service=grants,
            settings=settings,
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        headers = {"Authorization": f"Bearer {grant.raw_token}"}
        transport_security = _mcp_transport_security(settings)
        assert transport_security.enable_dns_rebinding_protection is True
        assert "notebookai.deequoique.tech" in transport_security.allowed_hosts
        assert "https://notebookai.deequoique.tech" in transport_security.allowed_origins
        assert "attacker.example" not in transport_security.allowed_hosts

        with TestClient(
            app, base_url="https://notebookai.deequoique.tech"
        ) as client:
            assert client.post("/mcp", json=initialize, headers=headers).status_code == 200
    finally:
        engine.dispose()


def test_stdio_subprocess_keeps_stdout_protocol_clean_and_serves_tools():
    source = r'''
from app.channels.types import TenantContext
from app.agent.types import AgentAnswer
from app.mcp_grants import McpGrantMetadata, ResolvedMcpGrant
from app.mcp_server import McpToolFacade, create_mcp_server, run_stdio

class Channel:
    async def handle(self, envelope):
        return AgentAnswer(status="ok", text="unused")

grant = ResolvedMcpGrant(
    McpGrantMetadata(
        grant_id="stdio-grant", app_user_id=1, scope="read",
        expires_at=None, revoked_at=None, disabled_at=None,
        created_at=None, updated_at=None, rotated_at=None,
        last_used_at=None, label=None, created_by=None,
    ),
    TenantContext(1, 2, "mcp", "mcp", "stdio-grant"),
)
run_stdio(
    server=create_mcp_server(
        scope="read", facade=McpToolFacade(grant=grant, channel_service=Channel())
    )
)
'''
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "stdio-test", "version": "1"},
        },
    }
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def send(value):
        assert process.stdin is not None
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def receive():
        assert process.stdout is not None
        lines: queue.Queue[str] = queue.Queue(maxsize=1)
        threading.Thread(
            target=lambda: lines.put(process.stdout.readline()),
            daemon=True,
        ).start()
        try:
            line = lines.get(timeout=5)
        except queue.Empty:
            pytest.fail("stdio MCP server did not return a response")
        assert line
        return json.loads(line)

    try:
        send(initialize)
        assert receive()["id"] == 1
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = receive()
        assert len(tools["result"]["tools"]) == 3
        send({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ask_notebook_agent",
                "arguments": {"question": "/start"},
            },
        })
        call = receive()
        assert call["result"]["structuredContent"]["error_code"] == "slash_command_not_allowed"
        assert process.stderr is not None
        assert process.stdout is not None
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        process.wait(timeout=5)
        # stdout is the protocol stream; diagnostics are intentionally kept
        # on stderr and never merged into it.
        assert process.stderr is not None
        # The helper configures diagnostics on stderr; stdout remains a
        # newline-delimited sequence of JSON-RPC objects above.
        process.stderr.read()
