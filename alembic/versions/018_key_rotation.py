"""Add API key rotation columns.

Supports zero-downtime key rotation: lineage (rotated_from), an optional
per-key auto-rotation interval, a configurable overlap/grace window, and the
timestamp a key was replaced. Idempotent -- skips columns that already exist
(create_all safety net).

Revision ID: 018_key_rotation
Revises: 017_policy_profiles
Create Date: 2026-02-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "018_key_rotation"
down_revision = "017_policy_profiles"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("rotated_from", sa.Column("rotated_from", sa.String(), nullable=True)),
    ("rotate_interval_days", sa.Column("rotate_interval_days", sa.Integer(), nullable=True)),
    ("rotation_grace_days", sa.Column("rotation_grace_days", sa.Integer(), nullable=False,
                                      server_default="7")),
    ("rotated_at", sa.Column("rotated_at", sa.DateTime(timezone=False), nullable=True)),
    ("rotation_notified_at", sa.Column("rotation_notified_at", sa.DateTime(timezone=False), nullable=True)),
]


def _table_exists(insp) -> bool:
    return "apikey" in insp.get_table_names()


def _column_exists(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _table_exists(insp):
        return
    for name, column in _COLUMNS:
        if not _column_exists(insp, "apikey", name):
            op.add_column("apikey", column)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _table_exists(insp):
        return
    for name, _column in reversed(_COLUMNS):
        if _column_exists(insp, "apikey", name):
            op.drop_column("apikey", name)
