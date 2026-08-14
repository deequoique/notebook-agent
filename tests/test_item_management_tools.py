"""Offline coverage for the bounded saved-item management surface.

The production schema is PostgreSQL (JSONB, ARRAY, pgvector, and native
enums), so this module creates a deliberately small SQLite compatibility
schema by hand.  SQLAlchemy can then exercise the real service predicates and
state transitions without requiring a local PostgreSQL daemon.  The schema is
only a test harness; it is not used by the application.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    ModelMessagesTypeAdapter,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.actions import AgentActionRuntime, AgentActionServices
from app.agent.management import (
    BatchItemOperationResult,
    InvalidCursor,
    InvalidWhySaved,
    ItemFilters,
    ItemNotFound,
    ItemOperationResult,
    KnowledgeItemManagementService,
    RecycleBinPurgeService,
    SavedItem,
    decode_cursor,
    encode_cursor,
)
from app.agent.runtime import build_agent
from app.agent.runtime import KnowledgeAgent
from app.agent.services import KnowledgeNotFound, KnowledgeServices
from app.agent.types import AgentRequest
from app.channels.pending_actions import PendingConfirmationService
from app.channels.conversations import reset_thread
from app.channels.service import ChannelService
from app.channels.types import ChannelEnvelope, TenantContext
from app.config import Settings
from app.connectors.base import Cue, ItemMeta, TextResult
from app.ingest.submission import IngestSubmissionService
from app.ingest.tasks import RawObjectStore, process_dispatch, process_item
from app.retrieval.search import bm25_search, vector_search
from app.models import (
    AppUser,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestDispatch,
    PendingChannelAction,
)
from migrations.versions import d4e5f6a7b8c9_item_management as management_migration


_CONTENT_DDL = """
CREATE TABLE content_item (
  id INTEGER PRIMARY KEY,
  public_id TEXT NOT NULL DEFAULT 'test-public-id',
  user_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  platform_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  author TEXT,
  published_at DATETIME,
  duration_sec INTEGER,
  char_count INTEGER,
  lang TEXT,
  description TEXT,
  tags TEXT,
  chapters TEXT,
  cover_url TEXT,
  saved_at DATETIME NOT NULL,
  archived_at DATETIME,
  why_saved TEXT,
  watch_state TEXT,
  watch_pos_sec INTEGER,
  content_hash TEXT,
  raw_object_key TEXT,
  raw_format TEXT NOT NULL DEFAULT 'json3',
  text_source TEXT NOT NULL,
  state TEXT NOT NULL,
  fail_reason TEXT,
  deleted_at DATETIME,
  purge_claimed_at DATETIME,
  purge_attempts INTEGER NOT NULL DEFAULT 0,
  purge_error_code TEXT,
  delete_claim_token TEXT
)
"""

_DISPATCH_DDL = """
CREATE TABLE ingest_dispatch (
  id INTEGER PRIMARY KEY,
  public_id TEXT NOT NULL,
  item_id INTEGER NOT NULL,
  source_thread_id INTEGER,
  request_key TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'pending',
  task_id TEXT,
  error_code TEXT,
  created_at DATETIME,
  updated_at DATETIME
)
"""

_PENDING_DDL = """
CREATE TABLE pending_channel_action (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  expires_at DATETIME NOT NULL,
  consumed_at DATETIME,
  consumed_message_id TEXT,
  cancelled_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_THREAD_DDL = """
CREATE TABLE app_user (id INTEGER PRIMARY KEY, created_at DATETIME, disabled_at DATETIME)
;
CREATE TABLE channel_identity (
  id INTEGER PRIMARY KEY, app_user_id INTEGER NOT NULL, channel TEXT NOT NULL,
  account_id TEXT NOT NULL, external_user_id TEXT NOT NULL,
  created_at DATETIME, disabled_at DATETIME
)
;
CREATE TABLE conversation_thread (
  id INTEGER PRIMARY KEY, public_id TEXT NOT NULL, app_user_id INTEGER NOT NULL,
  channel_identity_id INTEGER NOT NULL, channel TEXT NOT NULL, account_id TEXT NOT NULL,
  external_conversation_id TEXT NOT NULL, created_at DATETIME, updated_at DATETIME,
  closed_at DATETIME
)
"""

_TURN_DDL = """
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
)
"""


@pytest.fixture
def sqlite_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(_CONTENT_DDL)
        connection.exec_driver_sql(_DISPATCH_DDL)
        connection.exec_driver_sql(_PENDING_DDL)
        connection.exec_driver_sql(_TURN_DDL)
        for statement in _THREAD_DDL.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)

    def factory():
        return Session(bind=engine, expire_on_commit=False)

    try:
        yield factory
    finally:
        engine.dispose()


def _tenant(user_id: int = 7, identity_id: int = 9) -> TenantContext:
    return TenantContext(user_id, identity_id, "telegram", "bot", f"external-{user_id}")


def _item(
    item_id: int,
    *,
    user_id: int = 7,
    saved_at: datetime | None = None,
    archived_at: datetime | None = None,
    deleted_at: datetime | None = None,
    state: str = "ready",
    why_saved: str | None = None,
    title: str | None = None,
    raw_object_key: str | None = None,
) -> ContentItem:
    # Keep a valid YouTube-shaped id so submission normalization can address
    # these rows while still giving each row a deterministic display title.
    platform_id = "dQw4w9WgXcQ"
    return ContentItem(
        id=item_id,
        user_id=user_id,
        platform="youtube",
        platform_id=platform_id,
        kind="video",
        url=f"https://www.youtube.com/watch?v={platform_id}",
        title=title or f"Title {item_id}",
        author="Author",
        duration_sec=42,
        saved_at=saved_at or datetime(2026, 8, 1, tzinfo=UTC),
        archived_at=archived_at,
        why_saved=why_saved,
        text_source="none",
        state=state,
        deleted_at=deleted_at,
        raw_object_key=raw_object_key,
        purge_attempts=0,
    )


def _seed_thread(factory, *, tenant: TenantContext | None = None) -> int:
    tenant = tenant or _tenant()
    with factory() as db:
        db.add(AppUser(id=tenant.app_user_id))
        db.add(
            ChannelIdentity(
                id=tenant.channel_identity_id,
                app_user_id=tenant.app_user_id,
                channel=tenant.channel,
                account_id=tenant.account_id,
                external_user_id=tenant.external_user_id,
            )
        )
        thread = ConversationThread(
            id=100 + tenant.app_user_id,
            public_id=f"thread-{tenant.app_user_id}",
            app_user_id=tenant.app_user_id,
            channel_identity_id=tenant.channel_identity_id,
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_conversation_id=f"chat-{tenant.app_user_id}",
        )
        db.add(thread)
        db.commit()
        return thread.id


def test_cursor_is_versioned_filter_bound_and_stably_ordered(sqlite_factory):
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    filters = ItemFilters(location="library")
    cursor = encode_cursor(filters=filters, timestamp=timestamp, item_id=12)
    assert decode_cursor(cursor, filters=filters) == (timestamp, 12)

    with pytest.raises(InvalidCursor):
        decode_cursor(cursor, filters=ItemFilters(state="ready"))
    with pytest.raises(InvalidCursor):
        decode_cursor(encode_cursor(filters=ItemFilters(location="trash"), timestamp=timestamp, item_id=12), filters=filters)
    with pytest.raises(InvalidCursor):
        decode_cursor("not-base64", filters=filters)

    with sqlite_factory() as db:
        db.add_all(
            [
                _item(1, saved_at=timestamp),
                _item(2, saved_at=timestamp),
                _item(3, saved_at=timestamp - timedelta(seconds=1)),
                _item(4, user_id=99, saved_at=timestamp + timedelta(days=1)),
            ]
        )
        db.commit()
    service = KnowledgeItemManagementService(sqlite_factory)
    page = service.list_items(_tenant(), limit=2, now=timestamp + timedelta(days=1))
    assert [row.item_id for row in page.items] == [2, 1]
    assert page.next_cursor
    next_page = service.list_items(
        _tenant(), limit=2, cursor=page.next_cursor, now=timestamp + timedelta(days=1)
    )
    assert [row.item_id for row in next_page.items] == [3]
    assert not next_page.next_cursor

    # The public bound is enforced even if a model supplies a huge limit.
    assert len(service.list_items(_tenant(), limit=1000).items) == 3


def test_management_projection_tenant_isolation_trash_and_why_saved(sqlite_factory):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add_all(
            [
                _item(11, why_saved="  keep   this  ", title="Visible"),
                _item(12, deleted_at=now - timedelta(days=2), title="Trash"),
                _item(13, user_id=99, title="Other tenant"),
                _item(14, why_saved="x" * 600, title="Legacy note"),
            ]
        )
        db.commit()
    service = KnowledgeItemManagementService(sqlite_factory, retention_days=30)
    library = service.list_items(_tenant(), now=now)
    assert [row.item_id for row in library.items] == [14, 11]
    assert library.items[1].why_saved == "  keep   this  "
    assert service.get_item(_tenant(), 14).why_saved == "x" * 600
    trash = service.list_items(_tenant(), location="trash", now=now)
    assert trash.items[0].item_id == 12
    assert trash.items[0].restorable is True
    assert trash.items[0].expires_at == now - timedelta(days=2) + timedelta(days=30)

    with pytest.raises(ItemNotFound):
        service.get_item(_tenant(), 13)
    with pytest.raises(ItemNotFound):
        service.get_item(_tenant(), 12)
    with pytest.raises(ItemNotFound):
        service.soft_delete(_tenant(), [13])
    with pytest.raises(ItemNotFound):
        service.restore(_tenant(), [13])
    with sqlite_factory() as db:
        assert db.get(ContentItem, 13).deleted_at is None

    updated = service.update_why_saved(_tenant(), 11, "  new   reason ")
    assert updated.status == "updated"
    unchanged = service.update_why_saved(_tenant(), 11, "new reason")
    assert unchanged.status == "unchanged"
    cleared = service.update_why_saved(_tenant(), 11, None)
    assert cleared.status == "updated"
    with pytest.raises(InvalidWhySaved):
        service.update_why_saved(_tenant(), 11, "x" * 501)


def test_web_archive_stays_hidden_from_channel_management_and_trash_restore_clears_it(
    sqlite_factory,
):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add(
            _item(
                14,
                archived_at=now - timedelta(days=1),
                title="Archived outside the active library",
            )
        )
        db.add(
            _item(
                15,
                archived_at=now - timedelta(days=2),
                deleted_at=now - timedelta(hours=1),
                title="Deleted state wins",
            )
        )
        db.commit()

    service = KnowledgeItemManagementService(sqlite_factory, retention_days=30)
    assert service.list_items(_tenant(), now=now).items == []
    assert [row.item_id for row in service.list_items(
        _tenant(), location="trash", now=now
    ).items] == [15]
    with pytest.raises(ItemNotFound):
        service.get_item(_tenant(), 14)
    with pytest.raises(ItemNotFound):
        service.update_why_saved(_tenant(), 14, "must stay hidden")
    with pytest.raises(ItemNotFound):
        service.request_delete_targets(_tenant(), [14])

    restored = service.restore(_tenant(), [15], now=now)
    assert restored.results[0].status == "restored"
    with sqlite_factory() as db:
        restored_item = db.get(ContentItem, 15)
        assert restored_item.deleted_at is None
        assert restored_item.archived_at is None
    assert [row.item_id for row in service.list_items(_tenant(), now=now).items] == [15]


def test_management_uses_database_now_for_retention_decisions(sqlite_factory):
    class RecordingSession:
        def __init__(self, inner, statements):
            self.inner = inner
            self.statements = statements

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def scalar(self, statement, *args, **kwargs):
            self.statements.append(str(statement))
            return self.inner.scalar(statement, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    statements = []
    base_factory = sqlite_factory

    def factory():
        return RecordingSession(base_factory(), statements)

    with base_factory() as db:
        db.add(_item(21))
        db.commit()
    service = KnowledgeItemManagementService(factory)
    service.list_items(_tenant())
    service.soft_delete(_tenant(), [21])
    assert any("now()" in statement.lower() for statement in statements)


def test_deleted_items_are_not_hydrated_or_sent_to_retrieval_backends(sqlite_factory):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add(_item(81, state="ready"))
        db.add(_item(82, deleted_at=now - timedelta(days=1), state="ready"))
        db.commit()
    services = KnowledgeServices(_tenant(), sqlite_factory)
    assert services.get_item(81).item_id == 81
    with pytest.raises(KnowledgeNotFound):
        services.get_item(82)

    class DB:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(str(statement))
            return SimpleNamespace(all=lambda: [])

    db = DB()
    assert vector_search(db, [0.1, 0.2], user_id=7, k=2) == []
    assert bm25_search(db, "term", user_id=7, k=2) == []
    assert all("deleted_at IS NULL" in statement for statement in db.statements)


def test_management_action_text_contains_bounded_rows_and_cursor():
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    row = SavedItem(
        item_id=41,
        platform="youtube",
        kind="video",
        title="A useful title",
        author="An author",
        url="https://www.youtube.com/watch?v=abc",
        duration_sec=60,
        saved_at=timestamp,
        why_saved="for later",
        ingestion_state="ready",
    )

    class Management:
        def list_items(self, tenant, **kwargs):
            return type("Page", (), {"items": [row], "next_cursor": "safe-cursor"})()

        def get_item(self, tenant, item_id):
            return row

    request = AgentRequest(
        question="库存",
        tenant=_tenant(),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="message",
        request_id="request",
    )
    actions = AgentActionRuntime(
        request,
        AgentActionServices(None, None, Management()),  # type: ignore[arg-type]
        enabled=False,
        management_enabled=True,
    )
    listed = actions.list_saved_items()
    assert listed.status == "ok"
    assert "#41" in listed.text and "A useful title" in listed.text
    assert "收藏原因：for later" in listed.text
    assert "safe-cursor" in listed.text
    detail = AgentActionRuntime(
        request,
        AgentActionServices(None, None, Management()),  # type: ignore[arg-type]
        enabled=False,
        management_enabled=True,
    ).get_saved_item(41)
    assert "https://www.youtube.com/watch?v=abc" in detail.text
    assert detail.results[0]["item_id"] == 41


@pytest.mark.asyncio
async def test_management_canonical_history_drives_next_page_on_second_agent_turn():
    first_row = SavedItem(
        item_id=61,
        platform="youtube",
        kind="video",
        title="First page",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ingestion_state="ready",
    )
    second_row = first_row.model_copy(update={"item_id": 62, "title": "Second page"})

    class Management:
        def __init__(self):
            self.cursors = []

        def list_items(self, tenant, **filters):
            self.cursors.append(filters.get("cursor"))
            if filters.get("cursor") is None:
                return type("Page", (), {"items": [first_row], "next_cursor": "safe-cursor"})()
            return type("Page", (), {"items": [second_row], "next_cursor": None})()

    management = Management()
    pending = SimpleNamespace(inspect_delete=lambda *_args: SimpleNamespace(active=False))
    actions = lambda request: AgentActionServices(None, pending, management)  # type: ignore[arg-type]
    calls = []

    def model(messages, _info):
        calls.append(messages)
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            return ModelResponse(parts=[TextPart("done")])
        has_cursor = "safe-cursor" in str(messages)
        arguments = {"cursor": "safe-cursor"} if has_cursor else {}
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "list_saved_items",
                    json.dumps(arguments),
                    tool_call_id=f"list-{len(calls)}",
                )
            ]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    runtime = KnowledgeAgent(FunctionModel(model), settings, lambda _request: object(), action_factory=actions)
    first_request = AgentRequest(
        question="我存了什么",
        tenant=_tenant(),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="m1",
        request_id="r1",
    )
    first = await runtime.run(first_request)
    assert "safe-cursor" in first.answer.text
    assert len(first.new_messages) == 2

    history = tuple(ModelMessagesTypeAdapter.dump_python(first.new_messages, mode="json"))
    second_request = AgentRequest(
        question="下一页",
        tenant=_tenant(),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="m2",
        request_id="r2",
        history=history,
    )
    second = await runtime.run(second_request)
    assert "Second page" in second.answer.text
    assert management.cursors == [None, "safe-cursor"]


@pytest.mark.asyncio
async def test_agent_delete_confirmation_code_fails_closed_for_stale_plain_reply(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(91, state="ready"))
        db.commit()
    management = KnowledgeItemManagementService(sqlite_factory)
    pending = PendingConfirmationService(sqlite_factory)
    services = AgentActionServices(None, pending, management)  # type: ignore[arg-type]

    def model(messages, _info):
        current = next(
            message
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
        )
        if any(isinstance(part, ToolReturnPart) for part in current.parts):
            return ModelResponse(parts=[TextPart("done")])
        text = " ".join(
            part.content
            for part in current.parts
            if hasattr(part, "content") and isinstance(part.content, str)
        )
        if "确认" in text:
            name, args = "confirm_item_deletion", {}
        else:
            name, args = "delete_saved_items", {"item_ids": [91]}
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(args), tool_call_id=name)]
        )

    settings = replace(
        Settings(), agent_item_management_enabled=True, agent_timeout_seconds=2
    )
    runtime = KnowledgeAgent(
        FunctionModel(model), settings, lambda _request: object(),
        action_factory=lambda _request: services,
    )

    def request(message_id, text, *, latest=None, history=()):
        return AgentRequest(
            question=text,
            tenant=tenant,
            thread_db_id=thread_id,
            thread_public_id="thread",
            message_id=message_id,
            request_id=f"request-{message_id}",
            latest_turn_message_id=latest,
            history=history,
        )

    first = await runtime.run(request("A", "删除 91"))
    second = await runtime.run(request("B", "删除 91"))
    code = re.search(r"确认删除 ([A-Z0-9]{6})", second.answer.text).group(1)
    stale = await runtime.run(request("stale", "确认删除", latest="B"))
    assert stale.answer.error_code == "confirmation_missing"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 91).deleted_at is None

    confirmed = await runtime.run(
        request("confirm", f"确认删除 {code}", latest="B")
    )
    assert confirmed.answer.error_code == "items_deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 91).deleted_at is not None
    assert "确认删除" in first.answer.text and "确认删除" in second.answer.text


def test_management_tool_schemas_have_only_safe_model_arguments():
    enabled = build_agent("test", management_enabled=True)
    disabled = build_agent("test", management_enabled=False)
    tools = enabled._function_toolset.tools
    assert {
        "list_saved_items",
        "get_saved_item",
        "update_saved_item",
        "delete_saved_items",
        "confirm_item_deletion",
        "restore_saved_items",
        "retry_item_ingestion",
    } <= set(tools)
    assert not {
        "list_saved_items",
        "get_saved_item",
        "delete_saved_items",
    } & set(disabled._function_toolset.tools)
    for name in (
        "list_saved_items",
        "get_saved_item",
        "update_saved_item",
        "delete_saved_items",
        "confirm_item_deletion",
        "restore_saved_items",
        "retry_item_ingestion",
    ):
        schema = tools[name].function_schema.json_schema
        assert not {"tenant", "user_id", "request_id", "thread_id", "task_id"} & set(schema.get("properties", {}))
    list_schema = tools["list_saved_items"].function_schema.json_schema
    assert list_schema["properties"]["limit"]["minimum"] == 1
    assert list_schema["properties"]["limit"]["maximum"] == 50
    assert list_schema["properties"]["cursor"]["anyOf"][0]["maxLength"] == 512
    why_schema = tools["update_saved_item"].function_schema.json_schema
    assert why_schema["properties"]["why_saved"]["anyOf"][0]["maxLength"] == 500
    for name in ("delete_saved_items", "restore_saved_items"):
        ids = tools[name].function_schema.json_schema["properties"]["item_ids"]
        assert ids["minItems"] == 1 and ids["maxItems"] == 10
        assert ids["items"]["exclusiveMinimum"] == 0
    assert tools["confirm_item_deletion"].function_schema.json_schema["properties"] == {}


class _DeleteManagement:
    def __init__(self):
        self.calls = 0
        self.fail_once = True

    def request_delete_targets(self, tenant, item_ids):
        return tuple(item_ids)

    def soft_delete(self, tenant, item_ids, **kwargs):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("provider detail must stay private")
        return BatchItemOperationResult(
            results=[ItemOperationResult(item_id=value, status="deleted") for value in item_ids]
        )


def test_delete_effect_failure_is_recoverable_and_replays_result(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    management = _DeleteManagement()
    service = PendingConfirmationService(sqlite_factory)
    requested = service.request_delete(
        tenant, thread_id, [41], management=management, request_message_id="request-a"
    )
    assert requested.confirmation_code is not None
    confirmation = f"确认删除 {requested.confirmation_code}"
    failed, result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes",
        message_text=confirmation,
        latest_turn_message_id="request-a",
        management=management,
    )
    assert failed.status == "effect_failed"
    assert result is None
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, requested.action_id)
        assert action is not None
        assert action.consumed_at is None
        assert action.payload["effect_state"] == "failed"

    confirmed, result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes",
        message_text=confirmation,
        latest_turn_message_id="request-a",
        management=management,
    )
    assert confirmed.status == "confirmed"
    assert result is not None
    replay, replay_result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes",
        message_text=confirmation,
        latest_turn_message_id="request-a",
        management=management,
    )
    assert replay.replayed and replay_result is None
    assert replay.results == confirmed.results
    assert management.calls == 2


def test_delete_code_rejects_stale_plain_confirmation_after_replacement(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    management = _DeleteManagement()
    management.fail_once = False
    service = PendingConfirmationService(sqlite_factory)
    first = service.request_delete(
        tenant, thread_id, [1], management=management, request_message_id="A"
    )
    second = service.request_delete(
        tenant, thread_id, [2], management=management, request_message_id="B"
    )
    assert first.action_id != second.action_id
    # A legacy/forged false flag must not restore the old plain-confirmation
    # behavior; every delete row is still code-gated.
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, second.action_id)
        assert action is not None
        payload = dict(action.payload)
        payload["requires_code"] = False
        action.payload = payload
        db.commit()
    missing, _ = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes",
        message_text="yes",
        latest_turn_message_id="B",
        management=management,
    )
    assert missing.status == "confirmation_missing"
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, second.action_id)
        assert action is not None
        confirmation_code = second.confirmation_code
        assert action.consumed_at is None
    wrong, _ = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes-2",
        message_text="确认删除 WRONG1",
        latest_turn_message_id="B",
        management=management,
    )
    assert wrong.status == "confirmation_missing"
    confirmed, _ = service.confirm_delete(
        tenant,
        thread_id,
        message_id="yes-3",
        message_text=f"确认删除 {confirmation_code}",
        latest_turn_message_id="B",
        management=management,
    )
    assert confirmed.status == "confirmed"


@pytest.mark.asyncio
async def test_channel_service_delete_anchor_advances_through_clarification(sqlite_factory):
    """Exercise A→B→C confirmation using the SQLite ChannelService harness."""

    tenant = _tenant()
    _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(92, state="ready"))
        db.commit()

    action_services = AgentActionServices(
        IngestSubmissionService(sqlite_factory, lambda _dispatch_id: "task"),
        PendingConfirmationService(sqlite_factory),
        KnowledgeItemManagementService(sqlite_factory),
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
        if prompt == "删除 92":
            name, arguments = "delete_saved_items", {"item_ids": [92]}
        elif prompt == "我还不确定":
            name, arguments = "clarify_item_deletion", {}
        elif prompt.startswith("确认删除"):
            name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(arguments), tool_call_id=name)]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(sqlite_factory, agent, settings)

    def envelope(message_id, text_value):
        return ChannelEnvelope(
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_user_id=tenant.external_user_id,
            conversation_id="chat-7",
            message_id=message_id,
            text=text_value,
        )

    first = await service.handle(envelope("A", "删除 92"))
    assert first.error_code == "confirmation_required"
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", first.text)
    assert code_match is not None
    code = code_match.group(1)
    second = await service.handle(envelope("B", "我还不确定"))
    assert second.error_code == "confirmation_required"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 92).deleted_at is None
        action = db.scalar(select(PendingChannelAction))
        assert action is not None
        assert action.payload["confirmation_anchor_message_id"] == "B"
        assert action.payload["confirmation_anchor_parent_message_id"] == "A"

    third = await service.handle(envelope("C", f"确认删除 {code}"))
    assert third.error_code == "items_deleted"
    assert third.action_results[0]["status"] == "deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 92).deleted_at is not None
        action = db.scalar(select(PendingChannelAction))
        assert action is not None and action.consumed_at is not None
        assert action.payload["confirmation_anchor_message_id"] == "C"
        assert action.payload["confirmation_anchor_parent_message_id"] == "B"


@pytest.mark.asyncio
async def test_channel_service_new_blocks_applying_delete_and_recovers_stale_claim(
    sqlite_factory,
):
    tenant = _tenant(user_id=8, identity_id=10)
    _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(93, user_id=8, state="ready"))
        db.commit()

    action_services = AgentActionServices(
        IngestSubmissionService(sqlite_factory, lambda _dispatch_id: "task"),
        PendingConfirmationService(sqlite_factory),
        KnowledgeItemManagementService(sqlite_factory),
    )

    observed_instructions: list[str] = []

    def model(messages, _info):
        observed_instructions.append(_info.instructions)
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
        if prompt == "删除 93":
            name, arguments = "delete_saved_items", {"item_ids": [93]}
        elif prompt.startswith("确认删除"):
            name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(arguments), tool_call_id=name)]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(sqlite_factory, agent, settings)

    def envelope(message_id, text_value):
        return ChannelEnvelope(
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_user_id=tenant.external_user_id,
            conversation_id="chat-8",
            message_id=message_id,
            text=text_value,
        )

    requested = await service.handle(envelope("A-new", "删除 93"))
    assert requested.error_code == "confirmation_required"
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", requested.text)
    assert code_match is not None
    code = code_match.group(1)
    with sqlite_factory() as db:
        action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.cancelled_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert action is not None
        payload = dict(action.payload)
        payload["effect_state"] = "applying"
        payload["effect_claimed_at"] = datetime.now(UTC).isoformat()
        payload["effect_claim_token"] = "in-flight"
        action.payload = payload
        db.commit()

    blocked = await service.handle(envelope("new", "/new"))
    assert blocked.status == "failed"
    assert blocked.error_code == "delete_in_progress"
    with sqlite_factory() as db:
        thread = db.get(ConversationThread, 108)
        action = db.scalar(select(PendingChannelAction))
        assert thread is not None and thread.closed_at is None
        assert action is not None and action.cancelled_at is None
        assert action.payload["effect_state"] == "applying"
        payload = dict(action.payload)
        payload["effect_claimed_at"] = (
            datetime.now(UTC) - timedelta(minutes=2)
        ).isoformat()
        action.expires_at = datetime.now(UTC) - timedelta(minutes=2)
        action.payload = payload
        db.commit()

    recovered = await service.handle(envelope("C-new", f"确认删除 {code}"))
    assert recovered.status == "ok"
    assert recovered.error_code == "items_deleted"
    assert any("等待删除确认" in value for value in observed_instructions)
    with sqlite_factory() as db:
        assert db.get(ContentItem, 93).deleted_at is not None
        action = db.scalar(select(PendingChannelAction))
        assert action is not None and action.consumed_at is not None


@pytest.mark.asyncio
async def test_channel_service_effect_failure_advances_anchor_for_retry(sqlite_factory):
    tenant = _tenant(user_id=15, identity_id=16)
    _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(95, user_id=15, state="ready"))
        db.commit()

    base = KnowledgeItemManagementService(sqlite_factory)

    class FailOnceManagement:
        def __init__(self):
            self.failed = False

        def request_delete_targets(self, tenant, item_ids):
            return base.request_delete_targets(tenant, item_ids)

        def soft_delete(self, tenant, item_ids, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("provider detail must stay private")
            return base.soft_delete(tenant, item_ids, **kwargs)

    management = FailOnceManagement()
    action_services = AgentActionServices(
        IngestSubmissionService(sqlite_factory, lambda _dispatch_id: "task"),
        PendingConfirmationService(sqlite_factory),
        management,
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
        if prompt == "删除 95":
            name, arguments = "delete_saved_items", {"item_ids": [95]}
        elif prompt.startswith("确认删除"):
            name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(arguments), tool_call_id=name)]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(sqlite_factory, agent, settings)

    def envelope(message_id, text_value):
        return ChannelEnvelope(
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_user_id=tenant.external_user_id,
            conversation_id="chat-15",
            message_id=message_id,
            text=text_value,
        )

    requested = await service.handle(envelope("A-fail", "删除 95"))
    assert requested.error_code == "confirmation_required"
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", requested.text)
    assert code_match is not None
    code = code_match.group(1)
    failed = await service.handle(envelope("B-fail", f"确认删除 {code}"))
    assert failed.status == "failed"
    assert failed.error_code == "delete_failed"
    with sqlite_factory() as db:
        action = db.scalar(select(PendingChannelAction))
        assert action is not None and action.consumed_at is None
        assert action.payload["effect_state"] == "failed"
        assert action.payload["confirmation_anchor_message_id"] == "B-fail"

    retried = await service.handle(envelope("C-fail", f"确认删除 {code}"))
    assert retried.status == "ok"
    assert retried.error_code == "items_deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 95).deleted_at is not None
        action = db.scalar(select(PendingChannelAction))
        assert action is not None and action.consumed_at is not None


@pytest.mark.asyncio
async def test_channel_service_replacement_code_reanchors_stale_reply(sqlite_factory):
    tenant = _tenant(user_id=17, identity_id=18)
    _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(96, user_id=17, state="ready"))
        db.commit()
    management = KnowledgeItemManagementService(sqlite_factory)
    action_services = AgentActionServices(
        IngestSubmissionService(sqlite_factory, lambda _dispatch_id: "task"),
        PendingConfirmationService(sqlite_factory),
        management,
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
        if prompt.startswith("删除"):
            name, arguments = "delete_saved_items", {"item_ids": [96]}
        elif prompt.startswith("确认"):
            name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(arguments), tool_call_id=name)]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(sqlite_factory, agent, settings)

    def envelope(message_id, text_value):
        return ChannelEnvelope(
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_user_id=tenant.external_user_id,
            conversation_id="chat-17",
            message_id=message_id,
            text=text_value,
        )

    first = await service.handle(envelope("A-code", "删除 96"))
    assert first.error_code == "confirmation_required"
    first_code_match = re.search(r"确认删除 ([A-Z0-9]{6})", first.text)
    assert first_code_match is not None
    first_code = first_code_match.group(1)
    replacement = await service.handle(envelope("B-code", "删除 96 再确认"))
    assert replacement.error_code == "confirmation_required"
    code_match = re.search(r"确认删除 ([A-Z0-9]{6})", replacement.text)
    assert code_match is not None
    code = code_match.group(1)
    with sqlite_factory() as db:
        action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.cancelled_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert action is not None
        assert action.payload["confirmation_anchor_message_id"] == "B-code"

    # Both a delayed plain reply and the code from the replaced action must
    # fail closed. Each failed attempt still advances the trusted anchor so
    # the current code can be entered on the following turn.
    stale = await service.handle(envelope("C-code", "确认"))
    assert stale.status == "failed"
    assert stale.error_code == "confirmation_missing"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 96).deleted_at is None
        action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.cancelled_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert action is not None and action.consumed_at is None
        assert action.payload["confirmation_anchor_message_id"] == "C-code"

    old_code = await service.handle(
        envelope("D-code", f"确认删除 {first_code}")
    )
    assert old_code.status == "failed"
    assert old_code.error_code == "confirmation_missing"
    with sqlite_factory() as db:
        action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.cancelled_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert action is not None and action.consumed_at is None
        assert action.payload["confirmation_anchor_message_id"] == "D-code"

    corrected = await service.handle(
        envelope("E-code", f"确认删除 {code}")
    )
    assert corrected.status == "ok"
    assert corrected.error_code == "items_deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 96).deleted_at is not None
        action = db.scalar(
            select(PendingChannelAction).order_by(PendingChannelAction.id.desc())
        )
        assert action is not None and action.consumed_at is not None


@pytest.mark.asyncio
async def test_channel_service_delete_code_lifecycle_cancel_consume_expire_replace(
    sqlite_factory,
):
    """Exercise the code lifecycle through the real ChannelService boundary."""

    tenant = _tenant(user_id=21, identity_id=22)
    _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add_all(
            [_item(98, user_id=21), _item(99, user_id=21), _item(100, user_id=21)]
        )
        db.commit()

    action_services = AgentActionServices(
        IngestSubmissionService(sqlite_factory, lambda _dispatch_id: "task"),
        PendingConfirmationService(sqlite_factory),
        KnowledgeItemManagementService(sqlite_factory),
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
        if prompt.startswith("删除"):
            if "100" in prompt:
                item_id = 100
            elif "99" in prompt:
                item_id = 99
            else:
                item_id = 98
            name, arguments = "delete_saved_items", {"item_ids": [item_id]}
        elif prompt == "取消":
            name, arguments = "cancel_item_deletion", {}
        elif prompt == "确认" or prompt.startswith("确认删除"):
            name, arguments = "confirm_item_deletion", {}
        else:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return ModelResponse(
            parts=[ToolCallPart(name, json.dumps(arguments), tool_call_id=name)]
        )

    settings = replace(
        Settings(),
        agent_item_management_enabled=True,
        agent_timeout_seconds=2,
    )
    agent = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: object(),
        action_factory=lambda _request: action_services,
    )
    service = ChannelService(sqlite_factory, agent, settings)

    def envelope(message_id, text_value):
        return ChannelEnvelope(
            channel=tenant.channel,
            account_id=tenant.account_id,
            external_user_id=tenant.external_user_id,
            conversation_id="chat-delete-lifecycle",
            message_id=message_id,
            text=text_value,
        )

    # A canceled request cannot be confirmed and is replaced by a fresh code.
    canceled_request = await service.handle(envelope("A-cancel", "删除 98"))
    assert canceled_request.error_code == "confirmation_required"
    canceled_code = re.search(
        r"确认删除 ([A-Z0-9]{6})", canceled_request.text
    )
    assert canceled_code is not None
    canceled = await service.handle(envelope("B-cancel", "取消"))
    assert canceled.error_code == "delete_cancelled"
    with sqlite_factory() as db:
        canceled_action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.consumed_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert canceled_action is not None and canceled_action.cancelled_at is not None
        assert db.get(ContentItem, 98).deleted_at is None

    # A new request receives a different code and a successful confirmation
    # consumes the action. Replaying the same ChannelService envelope is
    # served from the durable turn instead of executing the effect again.
    replacement = await service.handle(envelope("C-consume", "删除 98"))
    current_code_match = re.search(
        r"确认删除 ([A-Z0-9]{6})", replacement.text
    )
    assert current_code_match is not None
    current_code = current_code_match.group(1)
    assert current_code != canceled_code.group(1)
    canceled_plain = await service.handle(envelope("D-cancel-plain", "确认"))
    assert canceled_plain.status == "failed"
    assert canceled_plain.error_code == "confirmation_missing"
    canceled_old_code = await service.handle(
        envelope("E-cancel-old", f"确认删除 {canceled_code.group(1)}")
    )
    assert canceled_old_code.status == "failed"
    assert canceled_old_code.error_code == "confirmation_missing"
    consumed = await service.handle(
        envelope("F-consume", f"确认删除 {current_code}")
    )
    assert consumed.error_code == "items_deleted"
    replay = await service.handle(
        envelope("F-consume", f"确认删除 {current_code}")
    )
    assert replay.model_dump() == consumed.model_dump()
    with sqlite_factory() as db:
        consumed_action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.id != canceled_action.id)
            .order_by(PendingChannelAction.id.desc())
        )
        assert consumed_action is not None and consumed_action.consumed_at is not None
        assert db.get(ContentItem, 98).deleted_at is not None

    # After A has been consumed, a new B request must not be consumable by A's
    # delayed plain reply or old code. B's current code is the only success.
    request_b = await service.handle(envelope("E-b", "删除 99"))
    b_code_match = re.search(r"确认删除 ([A-Z0-9]{6})", request_b.text)
    assert b_code_match is not None
    b_code = b_code_match.group(1)
    late_a_plain = await service.handle(envelope("F-b-plain", "确认"))
    assert late_a_plain.status == "failed"
    assert late_a_plain.error_code == "confirmation_missing"
    late_a_code = await service.handle(
        envelope("G-b-old", f"确认删除 {current_code}")
    )
    assert late_a_code.status == "failed"
    assert late_a_code.error_code == "confirmation_missing"
    finished_b = await service.handle(
        envelope("H-b-current", f"确认删除 {b_code}")
    )
    assert finished_b.status == "ok"
    assert finished_b.error_code == "items_deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 99).deleted_at is not None

    # Expire an old action, then create a replacement. A late plain reply and
    # the old code are both rejected before the new code succeeds.
    expired_request = await service.handle(envelope("I-expire", "删除 100"))
    expired_code_match = re.search(
        r"确认删除 ([A-Z0-9]{6})", expired_request.text
    )
    assert expired_code_match is not None
    expired_code = expired_code_match.group(1)
    with sqlite_factory() as db:
        expired_action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.consumed_at.is_(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert expired_action is not None
        expired_action.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    expired = await service.handle(
        envelope("J-expire", f"确认删除 {expired_code}")
    )
    assert expired.status == "failed"
    assert expired.error_code == "confirmation_expired"

    fresh_request = await service.handle(envelope("K-fresh", "删除 100"))
    fresh_code_match = re.search(
        r"确认删除 ([A-Z0-9]{6})", fresh_request.text
    )
    assert fresh_code_match is not None
    fresh_code = fresh_code_match.group(1)
    assert fresh_code != expired_code

    late_plain = await service.handle(envelope("L-plain", "确认"))
    assert late_plain.status == "failed"
    assert late_plain.error_code == "confirmation_missing"
    late_old_code = await service.handle(
        envelope("M-old", f"确认删除 {expired_code}")
    )
    assert late_old_code.status == "failed"
    assert late_old_code.error_code == "confirmation_missing"
    finished = await service.handle(
        envelope("N-current", f"确认删除 {fresh_code}")
    )
    assert finished.status == "ok"
    assert finished.error_code == "items_deleted"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 100).deleted_at is not None
        fresh_action = db.scalar(
            select(PendingChannelAction)
            .where(PendingChannelAction.consumed_at.is_not(None))
            .order_by(PendingChannelAction.id.desc())
        )
        assert fresh_action is not None


def test_delete_applying_claim_is_single_owner_and_stale_claim_recovers(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    management = _DeleteManagement()
    management.fail_once = False
    service = PendingConfirmationService(sqlite_factory)
    requested = service.request_delete(
        tenant, thread_id, [3], management=management, request_message_id="claim-request"
    )
    assert requested.confirmation_code is not None
    confirmation = f"确认删除 {requested.confirmation_code}"
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, requested.action_id)
        assert action is not None
        payload = dict(action.payload)
        payload.update(
            {
                "effect_state": "applying",
                "effect_claimed_at": datetime.now(UTC).isoformat(),
                "effect_claim_token": "a" * 32,
            }
        )
        action.payload = payload
        db.commit()

    in_progress, result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="claim-1",
        message_text=confirmation,
        latest_turn_message_id="claim-request",
        management=management,
    )
    assert in_progress.status == "effect_in_progress"
    assert result is None
    assert management.calls == 0

    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, requested.action_id)
        assert action is not None
        payload = dict(action.payload)
        payload["effect_claimed_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        action.payload = payload
        db.commit()
    recovered, result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="claim-2",
        message_text=confirmation,
        latest_turn_message_id="claim-request",
        management=management,
    )
    assert recovered.status == "confirmed"
    assert result is not None
    assert management.calls == 1


def test_expired_applying_action_stays_trusted_and_cannot_be_replaced(sqlite_factory):
    tenant = _tenant(user_id=19, identity_id=20)
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(97, user_id=19, state="ready"))
        db.commit()
    management = KnowledgeItemManagementService(sqlite_factory)
    service = PendingConfirmationService(sqlite_factory)
    requested = service.request_delete(
        tenant,
        thread_id,
        [97],
        management=management,
        request_message_id="expired-A",
    )
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, requested.action_id)
        assert action is not None
        payload = dict(action.payload)
        payload.update(
            {
                "effect_state": "applying",
                "effect_claimed_at": (
                    datetime.now(UTC) - timedelta(minutes=2)
                ).isoformat(),
                "effect_claim_token": "expired-claim",
            }
        )
        action.payload = payload
        action.expires_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()

    snapshot = service.inspect_delete(tenant, thread_id)
    assert snapshot.active and snapshot.count == 1
    replacement = service.request_save(
        tenant,
        thread_id,
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    )
    assert replacement.status == "effect_in_progress"
    replacement_delete = service.request_delete(
        tenant,
        thread_id,
        [97],
        management=management,
        request_message_id="expired-replace",
    )
    assert replacement_delete.status == "effect_in_progress"
    assert service.cancel_delete(tenant, thread_id).status == "effect_in_progress"
    clarification = service.clarify_delete(
        tenant,
        thread_id,
        message_id="expired-B",
        latest_turn_message_id="expired-A",
    )
    assert clarification.status == "confirmation_required"


def test_delete_applying_rejects_replacement_and_cancel_races(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add_all([_item(4), _item(5)])
        db.commit()
    base = KnowledgeItemManagementService(sqlite_factory, retention_days=30)
    service = PendingConfirmationService(sqlite_factory)
    interleaved = {}

    class InterleavingManagement:
        def request_delete_targets(self, tenant, item_ids):
            return base.request_delete_targets(tenant, item_ids)

        def soft_delete(self, tenant, item_ids, **kwargs):
            # ``confirm_delete`` has committed the applying claim before it
            # invokes the external effect. Both operations must fail closed
            # rather than replacing/cancelling the action in this window.
            interleaved["replacement"] = service.request_delete(
                tenant,
                thread_id,
                [5],
                management=self,
                request_message_id="replacement",
            )
            interleaved["save"] = service.request_save(
                tenant,
                thread_id,
                ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            )
            interleaved["cancel"] = service.cancel_delete(tenant, thread_id)
            return base.soft_delete(tenant, item_ids, **kwargs)

    management = InterleavingManagement()
    requested = service.request_delete(
        tenant,
        thread_id,
        [4],
        management=management,
        request_message_id="applying-request",
    )
    confirmed, result = service.confirm_delete(
        tenant,
        thread_id,
        message_id="applying-confirm",
        message_text=f"确认删除 {requested.confirmation_code}",
        latest_turn_message_id="applying-request",
        management=management,
    )
    assert confirmed.status == "confirmed"
    assert result is not None
    assert interleaved["replacement"].status == "effect_in_progress"
    assert interleaved["replacement"].error_code == "delete_in_progress"
    assert interleaved["save"].status == "effect_in_progress"
    assert interleaved["cancel"].status == "effect_in_progress"
    with sqlite_factory() as db:
        action = db.get(PendingChannelAction, requested.action_id)
        assert action is not None and action.cancelled_at is None
        assert action.consumed_at is not None
        assert db.get(ContentItem, 4).deleted_at is not None
        assert db.get(ContentItem, 5).deleted_at is None


def test_save_restore_early_return_is_committed_and_retry_queue_failure_is_retryable(sqlite_factory):
    tenant = _tenant()
    deleted = datetime(2026, 8, 7, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add(_item(51, deleted_at=deleted, state="ready"))
        db.add(_item(52, state="failed"))
        db.add(_item(53, user_id=99, state="failed"))
        db.add(
            IngestDispatch(
                id=601,
                public_id="old-dispatch",
                item_id=52,
                request_key="old",
                attempt=1,
                state="failed",
                error_code="ingestion_failed",
            )
        )
        db.add(_item(54, state="pending"))
        db.add(
            IngestDispatch(
                id=602,
                public_id="split-dispatch",
                item_id=54,
                request_key="split-old",
                attempt=1,
                state="failed",
                error_code="queue_unavailable",
            )
        )
        db.commit()
    service = IngestSubmissionService(sqlite_factory, lambda _id: "task")
    restored = service.submit_urls(
        tenant,
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        why_saved="  restored   reason ",
        request_key="restore-request",
    )
    assert restored.results[0].status == "restored"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 51)
        assert item.deleted_at is None
        assert item.why_saved == "restored reason"

    failing = IngestSubmissionService(sqlite_factory, lambda _id: (_ for _ in ()).throw(RuntimeError("broker private")))
    cross_tenant = failing.retry_item(tenant, 53, request_key="cross-tenant")
    assert cross_tenant.status == "retry_not_allowed"
    retry = failing.retry_item(tenant, 52, request_key="retry-request")
    assert retry.status == "queue_unavailable"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 52)
        dispatch = db.scalar(select(IngestDispatch).where(IngestDispatch.request_key == "retry-request"))
        assert item.state == "failed"
        assert dispatch.state == "failed"
        assert dispatch.error_code == "queue_unavailable"

    # A fresh retry key also repairs a split state left by an earlier
    # queue-failure commit before creating the next attempt.
    repairing = IngestSubmissionService(sqlite_factory, lambda _id: "task")
    repaired = repairing.retry_item(tenant, 54, request_key="split-retry")
    assert repaired.status == "queued"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 54)
        dispatch = db.scalar(
            select(IngestDispatch).where(IngestDispatch.request_key == "split-retry")
        )
        assert item.state == "pending"
        assert dispatch.state == "enqueued"


def test_delete_restore_and_resave_converge_on_same_item(sqlite_factory):
    tenant = _tenant()
    with sqlite_factory() as db:
        db.add(_item(56, state="ready", raw_object_key="7/youtube/dQw4w9WgXcQ/raw.json3"))
        db.commit()
    management = KnowledgeItemManagementService(sqlite_factory, retention_days=30)
    deleted = management.soft_delete(tenant, [56], now=datetime(2026, 8, 8, tzinfo=UTC))
    assert deleted.results[0].status == "deleted"
    restored = management.restore(tenant, [56], now=datetime(2026, 8, 9, tzinfo=UTC))
    assert restored.results[0].status == "restored"

    # A repeated save is an idempotent restore/re-save, not a duplicate item.
    with sqlite_factory() as db:
        item = db.get(ContentItem, 56)
        item.deleted_at = datetime(2026, 8, 8, tzinfo=UTC)
        db.commit()
    submit = IngestSubmissionService(sqlite_factory, lambda _id: "task")
    result = submit.submit_urls(
        tenant,
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        why_saved="after restore",
        request_key="resave-request",
    )
    assert result.results[0].status == "restored"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 56)
        assert item.deleted_at is None
        assert item.raw_object_key.endswith("raw.json3")


def test_aborted_delete_leaves_restore_and_resave_path_available(sqlite_factory):
    tenant = _tenant()
    thread_id = _seed_thread(sqlite_factory, tenant=tenant)
    with sqlite_factory() as db:
        db.add(_item(57, state="failed"))
        db.commit()
    management = KnowledgeItemManagementService(sqlite_factory, retention_days=30)

    class AbortAfterEffect:
        def __init__(self):
            self.abort = True

        def request_delete_targets(self, tenant, item_ids):
            return management.request_delete_targets(tenant, item_ids)

        def soft_delete(self, tenant, item_ids, **kwargs):
            result = management.soft_delete(
                tenant, item_ids, effect_token=kwargs.get("effect_token")
            )
            if self.abort:
                self.abort = False
                raise RuntimeError("abort after durable effect")
            return result

    pending = PendingConfirmationService(sqlite_factory)
    aborting = AbortAfterEffect()
    request = pending.request_delete(
        tenant,
        thread_id,
        [57],
        management=aborting,
        request_message_id="abort-request",
    )
    assert request.confirmation_code is not None
    confirmation = f"确认删除 {request.confirmation_code}"
    failed, _ = pending.confirm_delete(
        tenant,
        thread_id,
        message_id="abort-confirm",
        message_text=confirmation,
        latest_turn_message_id="abort-request",
        management=aborting,
    )
    assert failed.status == "effect_failed"
    assert management.restore(tenant, [57], now=datetime(2026, 8, 9, tzinfo=UTC)).results[0].status == "restored"
    late, _ = pending.confirm_delete(
        tenant,
        thread_id,
        message_id="abort-retry",
        message_text=confirmation,
        latest_turn_message_id="abort-request",
        management=aborting,
    )
    assert late.status == "confirmed"
    with sqlite_factory() as db:
        assert db.get(ContentItem, 57).deleted_at is None
    submit = IngestSubmissionService(sqlite_factory, lambda _id: "task")
    result = submit.submit_urls(
        tenant,
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        why_saved=None,
        request_key="after-abort",
    )
    # The item was restored and remains retryable, so this is never a second
    # physical ContentItem row even though the failed confirmation was retried.
    assert result.results[0].item_id == 57


def test_sqlalchemy_worker_deleted_result_converges_then_restore_resaves_new_dispatch(
    sqlite_factory,
):
    tenant = _tenant(user_id=11, identity_id=12)
    with sqlite_factory() as db:
        db.add(_item(58, user_id=11, state="chunking"))
        db.add(
            IngestDispatch(
                id=603,
                public_id="worker-delete",
                item_id=58,
                request_key="worker-delete-request",
                attempt=1,
                state="pending",
            )
        )
        db.commit()

    state = process_dispatch(
        603,
        task_id="worker-task",
        processor=lambda _item_id: "deleted",
        session_factory=sqlite_factory,
    )
    assert state == "deleted"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 58)
        dispatch = db.get(IngestDispatch, 603)
        assert item.state == "failed"
        assert item.fail_reason == "item_deleted"
        assert dispatch.state == "failed"
        assert dispatch.error_code == "item_deleted"

    management = KnowledgeItemManagementService(sqlite_factory, retention_days=30)
    assert management.soft_delete(
        tenant, [58], now=datetime(2026, 8, 8, tzinfo=UTC)
    ).results[0].status == "deleted"
    assert management.restore(
        tenant, [58], now=datetime(2026, 8, 9, tzinfo=UTC)
    ).results[0].status == "restored"

    submitted = IngestSubmissionService(sqlite_factory, lambda _id: "new-task")
    result = submitted.submit_urls(
        tenant,
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        why_saved=None,
        request_key="worker-resave",
    )
    assert result.results[0].status == "queued"
    with sqlite_factory() as db:
        item = db.get(ContentItem, 58)
        dispatches = list(
            db.scalars(
                select(IngestDispatch)
                .where(IngestDispatch.item_id == 58)
                .order_by(IngestDispatch.attempt)
            )
        )
        assert item.state == "pending"
        assert len(dispatches) == 2
        assert dispatches[-1].request_key == "worker-resave"
        assert dispatches[-1].state == "enqueued"


def test_late_worker_deletes_object_after_delete_race():
    class Item:
        id = 41
        user_id = 7
        platform = "youtube"
        platform_id = "video-41"
        url = "https://www.youtube.com/watch?v=video-41"
        title = author = published_at = duration_sec = lang = description = None
        tags = chapters = cover_url = None
        state = "pending"
        deleted_at = None
        purge_claimed_at = None
        raw_object_key = None
        content_hash = None
        text_source = "none"
        fail_reason = None

    item = Item()
    commits = []
    refreshes = 0

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, model, item_id):
            return item

        def commit(self):
            commits.append((item.state, item.raw_object_key))

        def refresh(self, value):
            nonlocal refreshes
            refreshes += 1
            if refreshes == 2:
                value.deleted_at = datetime.now(UTC)

    class Connector:
        def fetch_meta(self, platform_id):
            return ItemMeta(platform_id=platform_id, url=item.url)

        def fetch_text(self, platform_id):
            return TextResult(b"raw", [Cue(0, 1, "text")], "official_cc", "en")

    class Embedder:
        def embed(self, values):
            return [[1.0, 0.0] for _ in values]

    class Store:
        def __init__(self):
            self.puts = []
            self.deletes = []

        def put(self, key, body, content_type):
            self.puts.append((key, body, content_type))

        def delete_object(self, key):
            self.deletes.append(key)

    store = Store()
    state = process_item(
        41,
        connector=Connector(),
        embedder=Embedder(),
        object_store=store,
        session_factory=lambda: DB(),
    )
    assert state == "deleted"
    assert store.puts and store.deletes == [store.puts[0][0]]
    assert any(state == "chunking" and key for state, key in commits)


def test_purge_defers_active_dispatch_releases_time_bound_claims_and_logs_safe_counters(sqlite_factory, monkeypatch, caplog):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    expired = now - timedelta(days=31)
    with sqlite_factory() as db:
        db.add_all([
            _item(71, deleted_at=expired, raw_object_key="private/key-71"),
            _item(72, deleted_at=expired, raw_object_key="private/key-72"),
            _item(73, deleted_at=expired, raw_object_key="private/key-73"),
        ])
        db.add(IngestDispatch(id=701, public_id="active", item_id=73, request_key="active", state="running"))
        db.commit()
    service = RecycleBinPurgeService(sqlite_factory, SimpleNamespace(delete_object=lambda _key: None), retention_days=30, batch_size=10, max_duration_seconds=0.5)
    claimed = service.claim_expired(now=now)
    assert claimed == (71, 72)
    assert service.claim_expired(now=now) == ()
    with sqlite_factory() as db:
        assert db.get(ContentItem, 73).purge_claimed_at is None
        # Make the rows available for the bounded-sweep assertion below.
        for item_id in (71, 72):
            db.get(ContentItem, item_id).purge_claimed_at = None
        db.commit()

    # Run a fresh sweep with a deterministic clock that exhausts its wall clock
    # budget before object work begins. Cleanup is skipped once the deadline is
    # exhausted; claim timeout makes these rows eligible for a later sweep.
    # Keep the prework (deferred count + claim) inside the budget, then
    # exhaust the sweep before the first object operation. Claims are reported
    # as deferred without an unbounded release transaction.
    ticks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    monkeypatch.setattr("app.agent.management.time.monotonic", lambda: next(ticks, 1.0))
    result = service.purge_once(now=now)
    assert result.claimed == 2
    assert result.deferred >= 1
    with sqlite_factory() as db:
        assert db.get(ContentItem, 71).purge_claimed_at is not None
        assert db.get(ContentItem, 72).purge_claimed_at is not None

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        ticks = iter((0.0, 0.0, 0.0, 0.0, 1.0))
        monkeypatch.setattr("app.agent.management.time.monotonic", lambda: next(ticks, 1.0))
        service.purge_once(now=now)
    payload = next(record.diagnostic_payload for record in caplog.records if getattr(record, "diagnostic_payload", {}).get("event") == "purge_sweep")
    assert payload["claimed"] >= 0
    assert "private/key-71" not in json.dumps(payload)
    assert "tenant" not in payload and "user_id" not in payload


def test_purge_prework_exhaustion_does_not_claim_or_touch_object_store(
    sqlite_factory, monkeypatch, caplog
):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    expired = now - timedelta(days=31)
    with sqlite_factory() as db:
        db.add(_item(74, deleted_at=expired, raw_object_key="private/key-74"))
        db.commit()

    deleted_keys: list[str] = []
    store = SimpleNamespace(delete_object=lambda key: deleted_keys.append(key))
    service = RecycleBinPurgeService(
        sqlite_factory,
        store,
        retention_days=30,
        batch_size=10,
        max_duration_seconds=0.5,
    )
    # The deferred-count prework observes an already-expired deadline.  The
    # sweep must not open a claim transaction or call the object adapter.
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr("app.agent.management.time.monotonic", lambda: next(ticks, 1.0))
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        result = service.purge_once(now=now)

    assert result.claimed == 0
    assert result.completed == 0
    assert result.failed == 0
    assert deleted_keys == []
    with sqlite_factory() as db:
        item = db.get(ContentItem, 74)
        assert item.purge_claimed_at is None
    payload = next(
        record.diagnostic_payload
        for record in caplog.records
        if getattr(record, "diagnostic_payload", {}).get("event") == "purge_sweep"
    )
    assert payload["timed_out"] is True
    assert payload["timeout_phase"] == "prework"
    assert payload["claimed"] == 0


def test_purge_object_delete_receives_only_remaining_sweep_budget(
    sqlite_factory, monkeypatch
):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    expired = now - timedelta(days=31)
    with sqlite_factory() as db:
        db.add(_item(75, deleted_at=expired, raw_object_key="private/key-75"))
        db.commit()

    calls: list[tuple[str, float | None]] = []

    class Store:
        def delete_object(self, key, *, timeout_seconds=None):
            calls.append((key, timeout_seconds))

    service = RecycleBinPurgeService(
        sqlite_factory,
        Store(),
        retention_days=30,
        batch_size=10,
        max_duration_seconds=0.5,
    )
    # started, count, count-check, claim, loop-check, row-read, object,
    # finalize; all values are below the 0.5-second deadline until object
    # deletion receives the remaining 0.1-second budget.
    ticks = iter((0.0, 0.1, 0.1, 0.2, 0.2, 0.3, 0.4, 0.4))
    monkeypatch.setattr("app.agent.management.time.monotonic", lambda: next(ticks, 0.4))
    result = service.purge_once(now=now)

    assert result.claimed == 1
    assert result.completed == 1
    assert calls and calls[0][0] == "private/key-75"
    assert calls[0][1] is not None
    assert 0 < calls[0][1] <= 0.11


def test_purge_object_adapter_typeerror_is_not_retried_without_timeout():
    calls: list[tuple[str, float | None]] = []
    fallback_calls: list[tuple[str, float | None]] = []

    class Store:
        def delete_object(self, key, *, timeout_seconds=None):
            calls.append((key, timeout_seconds))
            raise TypeError("provider implementation failure")

        def delete(self, key, *, timeout_seconds=None):
            fallback_calls.append((key, timeout_seconds))

    service = RecycleBinPurgeService(
        lambda: None,  # the adapter call is resolved before a DB is needed
        Store(),
        max_duration_seconds=1,
    )
    with pytest.raises(TypeError, match="provider implementation failure"):
        service._delete_object("private/key", timeout_seconds=0.25)
    assert calls == [("private/key", 0.25)]
    assert fallback_calls == []


def test_purge_object_adapter_without_timeout_fails_safe_before_call():
    calls: list[str] = []

    class Store:
        def delete_object(self, key):
            calls.append(key)

    service = RecycleBinPurgeService(
        lambda: None,
        Store(),
        max_duration_seconds=1,
    )
    with pytest.raises(RuntimeError, match="object_delete_timeout_unsupported"):
        service._delete_object("private/key", timeout_seconds=0.25)
    assert calls == []


def test_purge_object_failure_marks_object_timeout_after_last_item(
    sqlite_factory, monkeypatch, caplog
):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add(
            _item(
                77,
                deleted_at=now - timedelta(days=31),
                raw_object_key="private/key-77",
            )
        )
        db.commit()

    clock = {"object_failed": False}

    def monotonic():
        return 1.0 if clock["object_failed"] else 0.0

    monkeypatch.setattr("app.agent.management.time.monotonic", monotonic)

    class Store:
        def delete_object(self, key, *, timeout_seconds=None):
            assert key == "private/key-77"
            assert timeout_seconds is not None
            clock["object_failed"] = True
            raise RuntimeError("provider unavailable")

    service = RecycleBinPurgeService(
        sqlite_factory,
        Store(),
        retention_days=30,
        batch_size=10,
        max_duration_seconds=0.5,
    )
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        result = service.purge_once(now=now)

    assert result.claimed == 1
    assert result.failed == 1
    payload = next(
        record.diagnostic_payload
        for record in caplog.records
        if getattr(record, "diagnostic_payload", {}).get("event") == "purge_sweep"
    )
    assert payload["timed_out"] is True
    assert payload["timeout_phase"] == "object_delete"


def test_purge_deadline_skips_cleanup_and_claim_timeout_reclaims_rows(
    sqlite_factory, monkeypatch, caplog
):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with sqlite_factory() as db:
        db.add(
            _item(
                76,
                deleted_at=now - timedelta(days=31),
                raw_object_key="private/key-76",
            )
        )
        db.commit()

    service = RecycleBinPurgeService(
        sqlite_factory,
        SimpleNamespace(delete_object=lambda _key: None),
        retention_days=30,
        batch_size=10,
        claim_timeout_seconds=1,
        max_duration_seconds=0.5,
    )
    ticks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    monkeypatch.setattr("app.agent.management.time.monotonic", lambda: next(ticks, 1.0))
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        result = service.purge_once(now=now)
    assert result.claimed == 1
    assert result.deferred == 1
    payload = next(
        record.diagnostic_payload
        for record in caplog.records
        if getattr(record, "diagnostic_payload", {}).get("event") == "purge_sweep"
    )
    assert payload["timed_out"] is True
    assert payload["timeout_phase"] == "processing"

    reclaimed = service.claim_expired(now=now + timedelta(seconds=2))
    assert reclaimed == (76,)


def test_purge_postgresql_claim_and_statement_timeout_compile_offline():
    statement = (
        select(ContentItem)
        .where(ContentItem.deleted_at.is_not(None))
        .order_by(ContentItem.deleted_at, ContentItem.id)
        .limit(10)
        .with_for_update(skip_locked=True)
    )
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in rendered

    captured: dict[str, object] = {}

    class Bind:
        class dialect:
            name = "postgresql"

    class DB:
        bind = Bind()

        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params

    RecycleBinPurgeService._set_statement_timeout(DB(), 0.137)
    assert "set_config('statement_timeout'" in captured["sql"]
    assert captured["params"] == {"timeout_text": "137ms"}
    compiled = str(
        text("SELECT set_config('statement_timeout', :timeout_text, true)").compile(
            dialect=postgresql.dialect()
        )
    )
    assert "set_config('statement_timeout'" in compiled
    assert "timeout_text" in compiled


def test_purge_claims_are_concurrency_safe_and_object_store_has_bounded_timeouts(monkeypatch):
    class SettingsProbe:
        minio_bucket = "kb"
        minio_endpoint_url = "http://minio"
        minio_access_key = "access"
        minio_secret_key = "secret"
        trash_purge_object_timeout_seconds = 3

    captured = {}

    class Boto:
        def client(self, service, **kwargs):
            captured.update(kwargs)
            return object()

    monkeypatch.setattr("app.object_store.get_settings", lambda: SettingsProbe())
    monkeypatch.setattr("app.object_store.boto3.client", Boto().client)
    RawObjectStore()
    config = captured["config"]
    assert config.connect_timeout == 3
    assert config.read_timeout == 3
    assert config.retries["total_max_attempts"] == 1


def test_raw_object_store_delete_uses_per_call_remaining_timeout(monkeypatch):
    class SettingsProbe:
        minio_bucket = "kb"
        minio_endpoint_url = "http://minio"
        minio_access_key = "access"
        minio_secret_key = "secret"
        trash_purge_object_timeout_seconds = 3

    clients = []

    class Client:
        def __init__(self):
            self.closed = False

        def delete_object(self, **_kwargs):
            return None

        def close(self):
            self.closed = True

    def client(_service, **kwargs):
        value = Client()
        clients.append((value, kwargs))
        return value

    monkeypatch.setattr("app.object_store.get_settings", lambda: SettingsProbe())
    monkeypatch.setattr("app.object_store.boto3.client", client)
    store = RawObjectStore()
    store.delete_object("private/key", timeout_seconds=0.137)
    store.delete("private/key-2", timeout_seconds=0.071)

    assert len(clients) == 3
    first_config = clients[0][1]["config"]
    dynamic_config = clients[1][1]["config"]
    second_dynamic_config = clients[2][1]["config"]
    assert first_config.connect_timeout == 3
    assert first_config.read_timeout == 3
    assert dynamic_config.connect_timeout + dynamic_config.read_timeout <= 0.137
    assert dynamic_config.connect_timeout > 0
    assert dynamic_config.read_timeout > 0
    assert second_dynamic_config.connect_timeout + second_dynamic_config.read_timeout <= 0.071
    assert second_dynamic_config.connect_timeout > 0
    assert second_dynamic_config.read_timeout > 0
    assert clients[1][0].closed is True
    assert clients[2][0].closed is True


def test_celery_maintenance_route_beat_and_migration_downgrade_guard(monkeypatch):
    from app.ingest.tasks import celery_app

    assert celery_app.conf.task_routes["app.ingest.tasks.purge_expired_items_task"]["queue"] == "maintenance"
    schedule = celery_app.conf.beat_schedule["purge-expired-items"]
    assert schedule["task"] == "app.ingest.tasks.purge_expired_items_task"
    assert schedule["options"]["queue"] == "maintenance"

    class Bind:
        def execute(self, statement):
            assert "deleted_at IS NOT NULL" in str(statement)
            return SimpleNamespace(scalar_one=lambda: 1)

    class Op:
        def get_bind(self):
            return Bind()

        def __getattr__(self, name):
            return lambda *args, **kwargs: pytest.fail(f"downgrade mutated schema via {name}")

    monkeypatch.setattr(management_migration, "op", Op())
    with pytest.raises(RuntimeError, match="refusing downgrade"):
        management_migration.downgrade()
