"""Upgrade from main-auth branch schema to v0.5.0

Revision ID: 003
Revises: 002
Create Date: 2026-03-28

This migration is specifically for deployments running the main-auth branch
where tables were created by SQLModel.metadata.create_all() (no prior Alembic
history).  It is fully idempotent — safe to run even if parts of the schema
are already up to date.

Steps performed:
  1. Owner table: add block_endpoints, is_active, description, max_keys
  2. APIKey table: rename owner -> owner_id, add FK to owner.id
  3. OwnerPermission: add unique constraint (owner_id, backend_name)

For fresh installs (started from v0.3+ with Alembic), migrations 001+002
already cover these changes and 003 is a no-op.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists in a table."""
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table)]
    return column in columns


def _constraint_exists(table: str, constraint_name: str) -> bool:
    """Check if a unique constraint already exists."""
    conn = op.get_bind()
    insp = inspect(conn)
    for uc in insp.get_unique_constraints(table):
        if uc["name"] == constraint_name:
            return True
    return False


def _fk_exists(table: str, fk_name: str) -> bool:
    """Check if a foreign key constraint already exists."""
    conn = op.get_bind()
    insp = inspect(conn)
    for fk in insp.get_foreign_keys(table):
        if fk.get("name") == fk_name:
            return True
    return False


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Owner table: add missing columns
    # -----------------------------------------------------------------------
    if not _column_exists("owner", "block_endpoints"):
        op.add_column("owner", sa.Column(
            "block_endpoints", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ))

    if not _column_exists("owner", "is_active"):
        op.add_column("owner", sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ))

    if not _column_exists("owner", "description"):
        op.add_column("owner", sa.Column(
            "description", sa.String(), nullable=True,
        ))

    if not _column_exists("owner", "max_keys"):
        op.add_column("owner", sa.Column(
            "max_keys", sa.Integer(), nullable=False,
            server_default=sa.text("5"),
        ))

    # -----------------------------------------------------------------------
    # 2. APIKey table: rename owner -> owner_id, add FK
    # -----------------------------------------------------------------------
    if _column_exists("apikey", "owner") and not _column_exists("apikey", "owner_id"):
        op.alter_column("apikey", "owner", new_column_name="owner_id")

    if not _fk_exists("apikey", "fk_apikey_owner_id"):
        try:
            op.create_foreign_key(
                "fk_apikey_owner_id",
                "apikey", "owner",
                ["owner_id"], ["id"],
                ondelete="CASCADE",
            )
        except Exception:
            pass  # FK may already exist under a different name

    # -----------------------------------------------------------------------
    # 3. OwnerPermission: add unique constraint
    # -----------------------------------------------------------------------
    if not _constraint_exists("ownerpermission", "uq_owner_backend_perm"):
        try:
            op.create_unique_constraint(
                "uq_owner_backend_perm",
                "ownerpermission",
                ["owner_id", "backend_name"],
            )
        except Exception:
            pass  # Constraint may exist under a different name


def downgrade() -> None:
    # This migration is idempotent — downgrade reverses only what was applied.
    try:
        op.drop_constraint("uq_owner_backend_perm", "ownerpermission", type_="unique")
    except Exception:
        pass

    try:
        op.drop_constraint("fk_apikey_owner_id", "apikey", type_="foreignkey")
    except Exception:
        pass

    if _column_exists("apikey", "owner_id") and not _column_exists("apikey", "owner"):
        op.alter_column("apikey", "owner_id", new_column_name="owner")

    for col in ("max_keys", "description", "is_active", "block_endpoints"):
        if _column_exists("owner", col):
            op.drop_column("owner", col)
