"""Add model_pricing table for per-model token cost management.

Revision ID: 009_add_model_pricing
Revises: 008_add_apikey_tpm_concurrency
Create Date: 2026-11-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "009_add_model_pricing"
down_revision = "008_add_apikey_tpm_concurrency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "model_pricing" not in tables:
        op.create_table(
            "model_pricing",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False, server_default="*"),
            sa.Column("input_cost_per_1m", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("output_cost_per_1m", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("cached_input_cost_per_1m", sa.Float(), nullable=True),
            sa.Column("source", sa.String(), nullable=False, server_default="manual"),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("model", "provider", name="uq_model_provider_pricing"),
        )
        op.create_index("ix_model_pricing_model", "model_pricing", ["model"])
        op.create_index("ix_model_pricing_provider", "model_pricing", ["provider"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "model_pricing" in tables:
        op.drop_index("ix_model_pricing_provider", table_name="model_pricing")
        op.drop_index("ix_model_pricing_model", table_name="model_pricing")
        op.drop_table("model_pricing")
