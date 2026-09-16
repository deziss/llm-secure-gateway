"""Add roadmap v060 tables and columns: FallbackChain, ModelAlias, SpendRecord, CachedResponse, and budget/weight columns

Revision ID: 004_roadmap_v060
Revises: 003_add_bot_platform
Create Date: 2026-09-16

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect

# --------------------------------------------------------------------------- #
# Alembic identifiers
# --------------------------------------------------------------------------- #
revision = "004_roadmap_v060"
down_revision = "003_add_bot_platform"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = insp.get_table_names()

    # 1. FallbackChain
    if "fallbackchain" not in tables:
        op.create_table(
            "fallbackchain",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("targets", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_fallbackchain_name"), "fallbackchain", ["name"], unique=True)

    # 2. ModelAlias
    if "modelalias" not in tables:
        op.create_table(
            "modelalias",
            sa.Column("alias", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("backend_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("fallback_chain_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["backend_name"], ["llmbackend.name"]),
            sa.ForeignKeyConstraint(["fallback_chain_id"], ["fallbackchain.id"]),
            sa.PrimaryKeyConstraint("alias"),
        )

    # 3. SpendRecord
    if "spendrecord" not in tables:
        op.create_table(
            "spendrecord",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("api_key_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_spendrecord_owner_id"), "spendrecord", ["owner_id"], unique=False)
        op.create_index(op.f("ix_spendrecord_api_key_hash"), "spendrecord", ["api_key_hash"], unique=False)
        op.create_index(op.f("ix_spendrecord_created_at"), "spendrecord", ["created_at"], unique=False)

    # 4. CachedResponse
    if "cachedresponse" not in tables:
        op.create_table(
            "cachedresponse",
            sa.Column("cache_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("response_json", sa.Text(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("cache_key"),
        )

    # 5. Add columns to existing tables
    if "llmbackend" in tables and not _column_exists("llmbackend", "weight"):
        op.add_column("llmbackend", sa.Column("weight", sa.Integer(), nullable=False, server_default="100"))

    if "owner" in tables and not _column_exists("owner", "monthly_budget_usd"):
        op.add_column("owner", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))

    if "apikey" in tables and not _column_exists("apikey", "monthly_budget_usd"):
        op.add_column("apikey", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = insp.get_table_names()

    if "apikey" in tables and _column_exists("apikey", "monthly_budget_usd"):
        op.drop_column("apikey", "monthly_budget_usd")

    if "owner" in tables and _column_exists("owner", "monthly_budget_usd"):
        op.drop_column("owner", "monthly_budget_usd")

    if "llmbackend" in tables and _column_exists("llmbackend", "weight"):
        op.drop_column("llmbackend", "weight")

    if "cachedresponse" in tables:
        op.drop_table("cachedresponse")

    if "spendrecord" in tables:
        op.drop_table("spendrecord")

    if "modelalias" in tables:
        op.drop_table("modelalias")

    if "fallbackchain" in tables:
        op.drop_table("fallbackchain")
