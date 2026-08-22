from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.context import ContextBuilder
from app.channels.types import TenantContext
from app.models import AppUser, ContentItem, ConversationThread, ConversationTurn


_CONTENT_DDL = """
CREATE TABLE content_item (
  id INTEGER PRIMARY KEY, public_id TEXT NOT NULL DEFAULT 'pub', user_id INTEGER NOT NULL,
  platform TEXT NOT NULL, platform_id TEXT NOT NULL, kind TEXT NOT NULL,
  url TEXT NOT NULL, title TEXT,
  author TEXT, published_at DATETIME, duration_sec INTEGER, char_count INTEGER,
  lang TEXT, description TEXT, tags TEXT, chapters TEXT, cover_url TEXT,
  saved_at DATETIME NOT NULL, why_saved TEXT, archived_at DATETIME,
  watch_state TEXT,
  watch_pos_sec INTEGER, content_hash TEXT, raw_object_key TEXT,
  raw_format TEXT NOT NULL DEFAULT 'json3',
  text_source TEXT NOT NULL, state TEXT NOT NULL, fail_reason TEXT,
  deleted_at DATETIME, purge_claimed_at DATETIME,
  purge_attempts INTEGER NOT NULL DEFAULT 0, purge_error_code TEXT,
  delete_claim_token TEXT
)
"""
_THREAD_DDL = """
CREATE TABLE app_user (id INTEGER PRIMARY KEY, created_at DATETIME, disabled_at DATETIME);
CREATE TABLE conversation_thread (
  id INTEGER PRIMARY KEY, public_id TEXT NOT NULL, app_user_id INTEGER NOT NULL,
  channel_identity_id INTEGER NOT NULL, channel TEXT NOT NULL, account_id TEXT NOT NULL,
  external_conversation_id TEXT NOT NULL, created_at DATETIME, updated_at DATETIME,
  closed_at DATETIME
)
"""
_TURN_DDL = """
CREATE TABLE conversation_turn (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, message_id TEXT NOT NULL,
  user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, sources TEXT NOT NULL,
  model_messages TEXT NOT NULL, answer_status TEXT NOT NULL DEFAULT 'legacy',
  error_code TEXT, action_results TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'completed', created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def _db(*, with_segment_table: bool = True) -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(_CONTENT_DDL)
        for statement in _THREAD_DDL.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.exec_driver_sql(_TURN_DDL)
        if with_segment_table:
            connection.exec_driver_sql(
                "CREATE TABLE segment (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL)"
            )
    return Session(engine, expire_on_commit=False)


def _tenant(user_id: int) -> TenantContext:
    return TenantContext(user_id, user_id + 100, "telegram", "bot", f"u-{user_id}")


def _thread(db: Session, thread_id: int, user_id: int) -> ConversationThread:
    thread = ConversationThread(
        id=thread_id,
        public_id=f"thread-{thread_id}",
        app_user_id=user_id,
        channel_identity_id=user_id + 100,
        channel="telegram",
        account_id="bot",
        external_conversation_id=f"chat-{thread_id}",
    )
    db.add(thread)
    return thread


def _item(
    item_id: int,
    user_id: int,
    *,
    state: str = "ready",
    deleted_at: datetime | None = None,
    title: str | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        user_id=user_id,
        platform="youtube",
        platform_id=f"video-{item_id}",
        kind="video",
        url=f"https://youtu.be/video-{item_id}",
        title=title or f"item {item_id}",
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
        text_source="none",
        state=state,
        deleted_at=deleted_at,
    )


def _inventory_row(item_id: int, title: str, *, state: str = "ready", deleted_at=None, safe_error_code=None):
    if deleted_at is not None:
        deleted_at = deleted_at.isoformat() if hasattr(deleted_at, "isoformat") else deleted_at
    return {
        "item_id": item_id,
        "platform": "youtube",
        "kind": "video",
        "title": title,
        "url": f"https://youtu.be/video-{item_id}",
        "ingestion_state": state,
        "deleted_at": deleted_at,
        "safe_error_code": safe_error_code,
    }


def test_context_builder_isolates_tenant_and_thread_and_filters_stale_rows():
    db = _db()
    now = datetime(2026, 8, 9, tzinfo=UTC)
    db.add_all([AppUser(id=1), AppUser(id=2), _thread(db, 11, 1), _thread(db, 22, 2)])
    db.add_all(
        [
            _item(101, 1, title="Ready one"),
            _item(102, 1, state="failed", title="Unavailable"),
            _item(103, 1, deleted_at=now, title="Deleted"),
            _item(201, 2, title="Other tenant"),
        ]
    )
    # Sources are only retained when their current segment/item remains
    # tenant-owned and ready.
    db.execute(text("INSERT INTO segment (id, item_id) VALUES (1, 101)"))
    db.add_all(
        [
            ConversationTurn(
                id=1,
                thread_id=11,
                message_id="m1",
                user_text="q",
                assistant_text="a",
                sources=[
                    {
                        "item_id": 101,
                        "segment_id": 1,
                        "title": "Ready one https://private.example/secret",
                        "excerpt": "safe excerpt",
                        "url": "https://youtu.be/video-101",
                    },
                    {
                        "item_id": 103,
                        "segment_id": 3,
                        "title": "Deleted",
                        "excerpt": "deleted",
                        "url": "https://youtu.be/video-103",
                    },
                ],
                model_messages=[],
                action_results=[
                    {
                        "items": [
                            _inventory_row(101, "Ready one"),
                            _inventory_row(102, "Unavailable", state="failed"),
                            _inventory_row(103, "Deleted", deleted_at=now),
                            # A mutation-like row must not become inventory.
                            {"item_id": 999, "status": "confirmation_required", "count": 1},
                        ],
                        "next_cursor": "cursor-secret",
                    }
                ],
                status="completed",
                created_at=now,
            )
        ]
    )
    db.commit()

    context = ContextBuilder().build(db, thread_id=11, tenant=_tenant(1))
    assert [item.item_id for item in context.recent_inventory] == [101]
    assert [source.item_id for source in context.recent_sources] == [101]
    projected = repr(asdict(context))
    assert "https://" not in projected
    assert "cursor-secret" not in projected
    assert "confirmation_required" not in projected

    # A valid thread id paired with another tenant must fail closed, and a
    # second tenant's thread cannot contribute to the first tenant's context.
    # Thread 22 is a fresh /new-style thread with no completed turns.
    assert ContextBuilder().build(db, thread_id=22, tenant=_tenant(2)).is_empty
    assert ContextBuilder().build(db, thread_id=11, tenant=_tenant(2)).is_empty
    assert ContextBuilder().build(db, thread_id=22, tenant=_tenant(1)).is_empty
    db.close()


def test_context_builder_preserves_inventory_order_and_caps_rows():
    db = _db()
    now = datetime(2026, 8, 9, tzinfo=UTC)
    db.add(AppUser(id=1))
    _thread(db, 11, 1)
    db.add_all([_item(101, 1), _item(102, 1), _item(103, 1)])
    db.add(
        ConversationTurn(
            id=1,
            thread_id=11,
            message_id="m1",
            user_text="q",
            assistant_text="a",
            sources=[],
            model_messages=[],
            action_results=[
                {
                    "items": [
                        _inventory_row(102, "second"),
                        _inventory_row(101, "first"),
                        _inventory_row(103, "third"),
                    ]
                }
            ],
            status="completed",
            created_at=now,
        )
    )
    db.commit()
    context = ContextBuilder(max_inventory_rows=2).build(
        db, thread_id=11, tenant=_tenant(1)
    )
    assert [(item.ordinal, item.item_id, item.title) for item in context.recent_inventory] == [
        (1, 102, "second"),
        (2, 101, "first"),
    ]
    db.close()


def test_context_builder_omits_source_focus_when_segment_ownership_query_fails():
    db = _db(with_segment_table=False)
    now = datetime(2026, 8, 9, tzinfo=UTC)
    db.add_all([AppUser(id=1), _thread(db, 11, 1), _item(101, 1, title="Ready")])
    db.add(
        ConversationTurn(
            id=1,
            thread_id=11,
            message_id="m1",
            user_text="q",
            assistant_text="a",
            sources=[
                {
                    "item_id": 101,
                    "segment_id": 999,
                    "title": "Unverified focus",
                    "excerpt": "must not survive",
                    "url": "https://youtu.be/video-101",
                }
            ],
            model_messages=[],
            action_results=[{"items": [_inventory_row(101, "Ready")]}],
            status="completed",
            created_at=now,
        )
    )
    db.commit()

    context = ContextBuilder().build(db, thread_id=11, tenant=_tenant(1))

    assert context.recent_sources == ()
    assert [item.item_id for item in context.recent_inventory] == [101]
    db.close()
