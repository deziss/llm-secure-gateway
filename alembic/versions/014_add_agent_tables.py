"""Add agent_runs and agent_steps tables (agent intelligence).

Revision ID: 014_add_agent_tables
Revises: 013_add_node_hosts
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "014_add_agent_tables"
down_revision = "013_add_node_hosts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "agent_runs" not in tables:
        op.create_table(
            "agent_runs",
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("agent", sa.String(length=64), nullable=False, server_default="unknown"),
            sa.Column("parent_agent", sa.String(length=64), nullable=True),
            sa.Column("session_key", sa.String(length=128), nullable=True),
            sa.Column("owner_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("first_ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("handoffs", sa.JSON(), nullable=False, server_default="[]"),
        )
        op.create_index("ix_agent_runs_session_key", "agent_runs", ["session_key"])
        op.create_index("ix_agent_runs_last_ts", "agent_runs", ["last_ts"])
        op.create_index("ix_agent_runs_agent", "agent_runs", ["agent"])

    if "agent_steps" not in tables:
        op.create_table(
            "agent_steps",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("kind", sa.String(length=8), nullable=False, server_default="llm"),
            sa.Column("agent", sa.String(length=64), nullable=False, server_default="unknown"),
            sa.Column("model", sa.String(length=128), nullable=False, server_default="unknown"),
            sa.Column("server", sa.String(length=64), nullable=True),
            sa.Column("owner_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
            sa.Column("dur_ms", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("ttft_ms", sa.Float(), nullable=True),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tool", sa.String(length=128), nullable=True),
            sa.Column("error", sa.String(length=256), nullable=True),
        )
        op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
        op.create_index("ix_agent_steps_run_seq", "agent_steps", ["run_id", "seq"])
        op.create_index("ix_agent_steps_ts", "agent_steps", ["ts"])


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "agent_steps" in tables:
        op.drop_table("agent_steps")
    if "agent_runs" in tables:
        op.drop_table("agent_runs")
