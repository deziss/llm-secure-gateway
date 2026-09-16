"""Add audit_log table for persistent audit trail

Revision ID: 004
Revises: 003
Create Date: 2026-03-30

Creates the audit_log table used when ENABLE_AUDIT_DB setting is true.
Idempotent — safe to run if the table already exists (e.g., from create_all).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "auditlog" not in insp.get_table_names():
        op.create_table(
            "auditlog",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
            sa.Column("event_type", sa.String(), nullable=False, index=True),
            sa.Column("identity", sa.String(), nullable=False, index=True),
            sa.Column("resource", sa.String(), nullable=False),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("auditlog")
