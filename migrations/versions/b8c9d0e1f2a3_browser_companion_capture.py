"""browser companion pairing and captured transcripts

Revision ID: b8c9d0e1f2a3
Revises: f1a2b3c4d5e6
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally forward-compatible. The
    # downgrade removes feature tables/columns but retains the harmless value.
    op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'ntu_kaltura'")
    op.add_column(
        "content_item",
        sa.Column("raw_format", sa.Text(), server_default="json3", nullable=False),
    )

    op.create_table(
        "browser_companion_pairing",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("challenge_hash", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger()),
        sa.Column("client_label", sa.Text(), nullable=False),
        sa.Column("client_version", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(client_label) BETWEEN 1 AND 200",
            name="ck_browser_companion_pairing_label",
        ),
        sa.CheckConstraint(
            "length(client_version) BETWEEN 1 AND 64",
            name="ck_browser_companion_pairing_version",
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("challenge_hash"),
    )
    op.create_index(
        "ix_browser_companion_pairing_expires",
        "browser_companion_pairing",
        ["expires_at"],
    )
    op.create_index(
        "ix_browser_companion_pairing_user",
        "browser_companion_pairing",
        ["app_user_id"],
    )

    op.create_table(
        "browser_companion_grant",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "scope", sa.Text(), server_default="capture:write", nullable=False
        ),
        sa.Column("client_label", sa.Text(), nullable=False),
        sa.Column("client_version", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope = 'capture:write'", name="ck_browser_companion_grant_scope"
        ),
        sa.CheckConstraint(
            "length(client_label) BETWEEN 1 AND 200",
            name="ck_browser_companion_grant_label",
        ),
        sa.CheckConstraint(
            "length(client_version) BETWEEN 1 AND 64",
            name="ck_browser_companion_grant_version",
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_browser_companion_grant_user",
        "browser_companion_grant",
        ["app_user_id"],
    )
    op.create_index(
        "ix_browser_companion_grant_active",
        "browser_companion_grant",
        ["token_hash", "revoked_at", "disabled_at"],
    )

    op.create_table(
        "browser_capture",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("dispatch_id", sa.BigInteger(), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.Text(), nullable=False),
        sa.Column("client_version", sa.Text(), nullable=False),
        sa.Column("caption_status", sa.Text(), nullable=False),
        sa.Column("caption_source", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_object_key", sa.Text()),
        sa.Column("content_hash", sa.Text()),
        sa.Column("state", sa.Text(), server_default="staging", nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "caption_status IN ('available', 'unavailable')",
            name="ck_browser_capture_caption_status",
        ),
        sa.CheckConstraint(
            "state IN ('staging', 'ready', 'failed')",
            name="ck_browser_capture_state",
        ),
        sa.CheckConstraint(
            "(caption_status = 'available' AND caption_source IN "
            "('official_cc', 'auto_caption') AND language IS NOT NULL) OR "
            "(caption_status = 'unavailable' AND caption_source IS NULL "
            "AND language IS NULL)",
            name="ck_browser_capture_caption_contract",
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["content_item.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id"], ["ingest_dispatch.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "app_user_id",
            "request_key",
            name="uq_browser_capture_user_request",
        ),
        sa.UniqueConstraint("dispatch_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_browser_capture_item_created",
        "browser_capture",
        ["item_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_browser_capture_state", "browser_capture", ["state", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_browser_capture_state", table_name="browser_capture")
    op.drop_index("ix_browser_capture_item_created", table_name="browser_capture")
    op.drop_table("browser_capture")
    op.drop_index(
        "ix_browser_companion_grant_active", table_name="browser_companion_grant"
    )
    op.drop_index(
        "ix_browser_companion_grant_user", table_name="browser_companion_grant"
    )
    op.drop_table("browser_companion_grant")
    op.drop_index(
        "ix_browser_companion_pairing_user", table_name="browser_companion_pairing"
    )
    op.drop_index(
        "ix_browser_companion_pairing_expires",
        table_name="browser_companion_pairing",
    )
    op.drop_table("browser_companion_pairing")
    op.drop_column("content_item", "raw_format")
