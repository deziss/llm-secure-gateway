"""Add bot platform tables

Revision ID: 003_add_bot_platform
Revises: 002_add_translation_mode
Create Date: 2026-05-21

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect

# --------------------------------------------------------------------------- #
# Alembic identifiers
# --------------------------------------------------------------------------- #
revision = "003_add_bot_platform"
down_revision = "002_add_translation_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = insp.get_table_names()

    # 1. Create llmbot table if it doesn't exist
    if "llmbot" not in tables:
        op.create_table(
            "llmbot",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("platform", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("encrypted_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("webhook_secret", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("backend_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("system_prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("history_limit", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_llmbot_name"), "llmbot", ["name"], unique=True)

    # 2. Create llmbotmessage table if it doesn't exist
    if "llmbotmessage" not in tables:
        op.create_table(
            "llmbotmessage",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("bot_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("content", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["bot_id"], ["llmbot.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_llmbotmessage_chat_id"), "llmbotmessage", ["chat_id"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = insp.get_table_names()

    if "llmbotmessage" in tables:
        op.drop_table("llmbotmessage")
    if "llmbot" in tables:
        op.drop_table("llmbot")
