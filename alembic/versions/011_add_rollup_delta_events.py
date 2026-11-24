"""Add delta_events counter to model_telemetry_rollups (SSE chunk events per minute).

Revision ID: 011_add_rollup_delta_events
Revises: 010_add_model_telemetry_rollups
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "011_add_rollup_delta_events"
down_revision = "010_add_model_telemetry_rollups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "model_telemetry_rollups" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("model_telemetry_rollups")}
    if "delta_events" not in cols:
        op.add_column(
            "model_telemetry_rollups",
            sa.Column("delta_events", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "model_telemetry_rollups" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("model_telemetry_rollups")}
    if "delta_events" in cols:
        op.drop_column("model_telemetry_rollups", "delta_events")
