"""Add per-owner policy_profiles table.

Per-tenant (per-owner) governance overrides: model allow/deny lists,
guardrail/PII switches, concurrency hint, informational daily budget.
Idempotent -- skips if the table already exists (create_all safety net).

Revision ID: 017_policy_profiles
Revises: 016_capture_columns
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "017_policy_profiles"
down_revision = "016_capture_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "policy_profiles" in insp.get_table_names():
        return

    op.create_table(
        "policy_profiles",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("allowed_models", sa.JSON(), nullable=True),
        sa.Column("denied_models", sa.JSON(), nullable=True),
        sa.Column("daily_budget_usd", sa.Float(), nullable=True),
        sa.Column("enforce_guardrails", sa.Boolean(), nullable=True),
        sa.Column("pii_masking", sa.Boolean(), nullable=True),
        sa.Column("max_concurrent_requests", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=True),
        sa.PrimaryKeyConstraint("owner_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "policy_profiles" in insp.get_table_names():
        op.drop_table("policy_profiles")
