"""Add node_hosts table (Infrastructure Intelligence telemetry).

Revision ID: 013_add_node_hosts
Revises: 012_add_alert_tables
Create Date: 2026-02-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "013_add_node_hosts"
down_revision = "012_add_alert_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    tables = set(insp.get_table_names())
    if "node_hosts" not in tables:
        op.create_table(
            "node_hosts",
            sa.Column("name", sa.String(length=64), primary_key=True),
            sa.Column("url", sa.String(length=255), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False,
                      server_default="node_exporter"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        indexes = {i["name"] for i in insp.get_indexes("node_hosts")}
        if "ix_node_hosts_name" not in indexes:
            op.create_index("ix_node_hosts_name", "node_hosts", ["name"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    if "node_hosts" in set(insp.get_table_names()):
        op.drop_index("ix_node_hosts_name", table_name="node_hosts")
        op.drop_table("node_hosts")
