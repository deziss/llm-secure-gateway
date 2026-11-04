"""Add rate_limit_tpm and max_concurrency columns to apikey table

Revision ID: 008_add_apikey_tpm_concurrency
Revises: 007_obs_integration
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "008_add_apikey_tpm_concurrency"
down_revision = "007_obs_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("apikey")} if "apikey" in insp.get_table_names() else set()
    if "rate_limit_tpm" not in cols:
        op.add_column("apikey", sa.Column("rate_limit_tpm", sa.Integer(), nullable=True))
    if "max_concurrency" not in cols:
        op.add_column("apikey", sa.Column("max_concurrency", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("apikey")} if "apikey" in insp.get_table_names() else set()
    if "max_concurrency" in cols:
        op.drop_column("apikey", "max_concurrency")
    if "rate_limit_tpm" in cols:
        op.drop_column("apikey", "rate_limit_tpm")
