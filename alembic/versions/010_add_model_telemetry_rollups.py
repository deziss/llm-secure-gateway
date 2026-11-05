"""Add model_telemetry_rollups table for multi-scale timeseries metrics.

Revision ID: 010_add_model_telemetry_rollups
Revises: 009_add_model_pricing
Create Date: 2026-11-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "010_add_model_telemetry_rollups"
down_revision = "009_add_model_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "model_telemetry_rollups" not in tables:
        op.create_table(
            "model_telemetry_rollups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("bucket_time", sa.DateTime(), nullable=False),
            sa.Column("server", sa.String(length=64), nullable=False),
            sa.Column("server_type", sa.String(length=32), nullable=False, server_default="unknown"),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ttft_sum_ms", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("ttft_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ttft_p50_ms", sa.Float(), nullable=True),
            sa.Column("ttft_p90_ms", sa.Float(), nullable=True),
            sa.Column("itl_sum_ms", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("itl_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_sum_ms", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("decode_tps_sum", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("decode_n", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("waiting_sum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("generating_sum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("samples_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("server_rps_sum", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("server_tok_s_sum", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("server_running_sum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("server_waiting_sum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("kv_cache_pct_sum", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("server_scrape_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("bucket_time", "server", "model", name="uq_telemetry_bucket_server_model"),
        )
        op.create_index("ix_telemetry_bucket_time", "model_telemetry_rollups", ["bucket_time"])
        op.create_index("ix_telemetry_server_model", "model_telemetry_rollups", ["server", "model"])
        op.create_index("ix_telemetry_model", "model_telemetry_rollups", ["model"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "model_telemetry_rollups" in tables:
        op.drop_index("ix_telemetry_model", table_name="model_telemetry_rollups")
        op.drop_index("ix_telemetry_server_model", table_name="model_telemetry_rollups")
        op.drop_index("ix_telemetry_bucket_time", table_name="model_telemetry_rollups")
        op.drop_table("model_telemetry_rollups")
