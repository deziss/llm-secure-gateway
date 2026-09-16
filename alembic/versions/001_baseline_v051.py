"""Baseline schema — complete v0.5.1 (idempotent)

Revision ID: 001_baseline_v051
Revises: (initial)
Create Date: 2026-04-03

This is the SINGLE baseline migration for llm-secure-gateway.  It creates the
complete v0.5.1 schema from scratch **and** handles upgrades from legacy
databases (v0.3.x SQL dumps, main-auth branch, etc.).

Every operation is guarded with existence checks — safe to run on:
  • A fresh (empty) database
  • A v0.3.x database imported from gateway_db.sql
  • A database previously managed by the old 001→004 migration chain

Tables managed:
  user, llmbackend, apikey, owner, providerkey, ownerpermission,
  systemsetting, invitecode, auditlog
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# --------------------------------------------------------------------------- #
# Alembic identifiers
# --------------------------------------------------------------------------- #
revision = "001_baseline_v051"
down_revision = None
branch_labels = None
depends_on = None

# Old revision IDs from the replaced migration chain
_OLD_REVISIONS = frozenset({"001_v01_to_v03", "002", "003", "004"})


# --------------------------------------------------------------------------- #
# Introspection helpers
# --------------------------------------------------------------------------- #
def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return name in insp.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return column in [c["name"] for c in insp.get_columns(table)]


def _constraint_exists(table: str, constraint_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    for uc in insp.get_unique_constraints(table):
        if uc["name"] == constraint_name:
            return True
    return False


def _fk_exists(table: str, fk_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    for fk in insp.get_foreign_keys(table):
        if fk.get("name") == fk_name:
            return True
    return False


def _index_exists(table: str, index_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return index_name in [idx["name"] for idx in insp.get_indexes(table)]


# --------------------------------------------------------------------------- #
# upgrade
# --------------------------------------------------------------------------- #
def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. user table (FastAPI-Users auth with RBAC roles)
    # ------------------------------------------------------------------ #
    if not _table_exists("user"):
        op.create_table(
            "user",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("hashed_password", sa.String(1024), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("role", sa.String(), nullable=False, server_default="VIEWER"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("last_login", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_user_email", "user", ["email"], unique=True)

    # ------------------------------------------------------------------ #
    # 2. llmbackend table
    # ------------------------------------------------------------------ #
    if not _table_exists("llmbackend"):
        op.create_table(
            "llmbackend",
            sa.Column("name", sa.String(), primary_key=True),
            sa.Column("base_url", sa.String(), nullable=False),
            sa.Column("backend_type", sa.String(), nullable=False, server_default="ollama"),
            sa.Column("api_key", sa.String(), nullable=True),
            sa.Column("models", sa.JSON(), nullable=True),
            sa.Column("fallback_urls", sa.JSON(), nullable=True, server_default="[]"),
            sa.Column("allowed_endpoints", sa.JSON(), nullable=True, server_default='["*"]'),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_llmbackend_name", "llmbackend", ["name"])
    else:
        # Ensure columns added in v0.3 exist on legacy tables
        if not _column_exists("llmbackend", "fallback_urls"):
            op.add_column("llmbackend", sa.Column("fallback_urls", sa.JSON(), nullable=True, server_default="[]"))
        if not _column_exists("llmbackend", "allowed_endpoints"):
            op.add_column("llmbackend", sa.Column("allowed_endpoints", sa.JSON(), nullable=True, server_default='["*"]'))

    # ------------------------------------------------------------------ #
    # 3. owner table
    # ------------------------------------------------------------------ #
    if not _table_exists("owner"):
        op.create_table(
            "owner",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("block_endpoints", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("max_keys", sa.Integer(), nullable=False, server_default=sa.text("5")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_owner_id", "owner", ["id"])
        op.create_index("ix_owner_user_id", "owner", ["user_id"])
    else:
        # v0.3.x → v0.5.1: add missing columns
        for col_name, col_def in [
            ("block_endpoints", sa.Column("block_endpoints", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
            ("is_active", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
            ("description", sa.Column("description", sa.String(), nullable=True)),
            ("max_keys", sa.Column("max_keys", sa.Integer(), nullable=False, server_default=sa.text("5"))),
        ]:
            if not _column_exists("owner", col_name):
                op.add_column("owner", col_def)

    # ------------------------------------------------------------------ #
    # 4. apikey table
    # ------------------------------------------------------------------ #
    if not _table_exists("apikey"):
        op.create_table(
            "apikey",
            sa.Column("key_hash", sa.String(), primary_key=True),
            sa.Column("prefix", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), sa.ForeignKey("owner.id", ondelete="CASCADE", name="fk_apikey_owner_id"), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("rate_limit_rpm", sa.Integer(), nullable=True, server_default="60"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )
        op.create_index("ix_apikey_key_hash", "apikey", ["key_hash"])
        op.create_index("ix_apikey_prefix", "apikey", ["prefix"])
        op.create_index("ix_apikey_owner_id", "apikey", ["owner_id"])
    else:
        # v0.3.x → v0.5.1: rename owner → owner_id and add FK
        if _column_exists("apikey", "owner") and not _column_exists("apikey", "owner_id"):
            op.alter_column("apikey", "owner", new_column_name="owner_id")

        if not _column_exists("apikey", "updated_at"):
            op.add_column("apikey", sa.Column("updated_at", sa.DateTime(), nullable=True))

        if not _fk_exists("apikey", "fk_apikey_owner_id"):
            try:
                op.create_foreign_key(
                    "fk_apikey_owner_id", "apikey", "owner",
                    ["owner_id"], ["id"], ondelete="CASCADE",
                )
            except Exception:
                pass  # FK may exist under a different auto-generated name

        if not _index_exists("apikey", "ix_apikey_prefix"):
            try:
                op.create_index("ix_apikey_prefix", "apikey", ["prefix"])
            except Exception:
                pass

        if not _index_exists("apikey", "ix_apikey_owner_id"):
            try:
                op.create_index("ix_apikey_owner_id", "apikey", ["owner_id"])
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 5. providerkey table
    # ------------------------------------------------------------------ #
    if not _table_exists("providerkey"):
        op.create_table(
            "providerkey",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.String(), sa.ForeignKey("owner.id"), nullable=False),
            sa.Column("provider_id", sa.String(), nullable=False),
            sa.Column("encrypted_key", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("owner_id", "provider_id", name="uq_owner_provider"),
        )
        op.create_index("ix_providerkey_owner_id", "providerkey", ["owner_id"])
        op.create_index("ix_providerkey_provider_id", "providerkey", ["provider_id"])

    # ------------------------------------------------------------------ #
    # 6. ownerpermission table
    # ------------------------------------------------------------------ #
    if not _table_exists("ownerpermission"):
        op.create_table(
            "ownerpermission",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.String(), sa.ForeignKey("owner.id"), nullable=False),
            sa.Column("backend_name", sa.String(), sa.ForeignKey("llmbackend.name"), nullable=False),
            sa.Column("allowed_models", sa.JSON(), nullable=True),
            sa.Column("allowed_endpoints", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("owner_id", "backend_name", name="uq_owner_backend_perm"),
        )
        op.create_index("ix_ownerpermission_owner_id", "ownerpermission", ["owner_id"])
        op.create_index("ix_ownerpermission_backend_name", "ownerpermission", ["backend_name"])
    else:
        # Add unique constraint if missing (v0.3.x → v0.5.1)
        if not _constraint_exists("ownerpermission", "uq_owner_backend_perm"):
            try:
                op.create_unique_constraint(
                    "uq_owner_backend_perm", "ownerpermission",
                    ["owner_id", "backend_name"],
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 7. systemsetting table
    # ------------------------------------------------------------------ #
    if not _table_exists("systemsetting"):
        op.create_table(
            "systemsetting",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    # ------------------------------------------------------------------ #
    # 8. invitecode table
    # ------------------------------------------------------------------ #
    if not _table_exists("invitecode"):
        op.create_table(
            "invitecode",
            sa.Column("code", sa.String(), primary_key=True),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("used_by", sa.String(), nullable=True),
            sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    # ------------------------------------------------------------------ #
    # 9. auditlog table (new in v0.5.0)
    # ------------------------------------------------------------------ #
    if not _table_exists("auditlog"):
        op.create_table(
            "auditlog",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("identity", sa.String(), nullable=False),
            sa.Column("resource", sa.String(), nullable=False),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
        )
        op.create_index("ix_auditlog_timestamp", "auditlog", ["timestamp"])
        op.create_index("ix_auditlog_event_type", "auditlog", ["event_type"])
        op.create_index("ix_auditlog_identity", "auditlog", ["identity"])


# --------------------------------------------------------------------------- #
# downgrade — drops everything (use only to wipe the database)
# --------------------------------------------------------------------------- #
def downgrade() -> None:
    for table in [
        "auditlog",
        "invitecode",
        "systemsetting",
        "ownerpermission",
        "providerkey",
        "apikey",
        "owner",
        "llmbackend",
        "user",
    ]:
        if _table_exists(table):
            op.drop_table(table)
