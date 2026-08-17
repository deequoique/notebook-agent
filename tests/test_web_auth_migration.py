from migrations.versions import d3f4a5b6c7d8_web_auth_library as migration

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import get_settings


def test_web_auth_migration_branches_from_agent_save_and_is_additive():
    assert migration.revision == "d3f4a5b6c7d8"
    assert migration.down_revision == "c7e8a91b2d34"


def test_web_and_ingest_completion_branches_converge_on_one_merge_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_revision("d3f4a5b6c7d8").down_revision == (
        "c7e8a91b2d34"
    )
    assert script.get_revision("d4e5f6a7b8c9").down_revision == (
        "c7e8a91b2d34"
    )
    assert script.get_revision("e5f6a7b8c9d0").down_revision == "d4e5f6a7b8c9"
    assert script.get_revision("f6a7b8c9d0e1").down_revision == "e5f6a7b8c9d0"
    assert script.get_heads() == ["b8c9d0e1f2a3"]
    assert script.get_revision("b8c9d0e1f2a3").down_revision == "f1a2b3c4d5e6"
    assert script.get_revision("f1a2b3c4d5e6").down_revision == "b2c3d4e5f6a7"


def test_web_auth_migration_backfills_and_roundtrips_in_isolated_database():
    try:
        base_url = make_url(get_settings().database_url)
    except RuntimeError as exc:
        pytest.skip(f"PostgreSQL configuration unavailable: {type(exc).__name__}")
    if not base_url.database:
        pytest.skip("configured PostgreSQL URL has no database")

    database_name = f"test_web_auth_migration_{uuid4().hex}"
    admin_engine = create_engine(
        base_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    target_engine = None
    created = False
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created = True
        except Exception as exc:
            pytest.skip(
                "isolated PostgreSQL database unavailable: "
                f"{type(exc).__name__}"
            )

        target_engine = create_engine(
            base_url.set(database=database_name), pool_pre_ping=True
        )
        config = Config("alembic.ini")
        with target_engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "c7e8a91b2d34")
            user_id = connection.scalar(
                text("INSERT INTO app_user DEFAULT VALUES RETURNING id")
            )
            legacy_id = connection.scalar(
                text(
                    """
                    INSERT INTO content_item (
                        user_id, platform, platform_id, kind, url
                    ) VALUES (
                        :user_id, 'youtube', 'legacy-video', 'video',
                        'https://www.youtube.com/watch?v=legacy-video'
                    ) RETURNING id
                    """
                ),
                {"user_id": user_id},
            )
            connection.commit()

            command.upgrade(config, migration.revision)
            public_id = connection.scalar(
                text("SELECT public_id FROM content_item WHERE id = :id"),
                {"id": legacy_id},
            )
            assert public_id and len(public_id) == 32
            inspector = inspect(connection)
            assert inspector.has_table("web_login_challenge")
            assert inspector.has_table("web_session")
            assert {"public_id", "archived_at"} <= {
                column["name"]
                for column in inspector.get_columns("content_item")
            }
            assert "requester_hash" in {
                column["name"]
                for column in inspector.get_columns("web_login_challenge")
            }
            assert "ix_content_item_saved_at" in {
                index["name"]
                for index in inspector.get_indexes("content_item")
            }
            assert "ix_ingest_dispatch_created_item" in {
                index["name"]
                for index in inspector.get_indexes("ingest_dispatch")
            }
            assert "ix_web_login_challenge_created_at" in {
                index["name"]
                for index in inspector.get_indexes("web_login_challenge")
            }

            command.downgrade(config, "c7e8a91b2d34")
            inspector = inspect(connection)
            assert not inspector.has_table("web_login_challenge")
            assert not inspector.has_table("web_session")
            assert "public_id" not in {
                column["name"]
                for column in inspector.get_columns("content_item")
            }
    finally:
        if target_engine is not None:
            target_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(f'DROP DATABASE "{database_name}" WITH (FORCE)')
                )
        admin_engine.dispose()
