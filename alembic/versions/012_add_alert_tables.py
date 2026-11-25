"""Add alert_rules and alert_incidents tables (alert & incident engine).

Revision ID: 012_add_alert_tables
Revises: 011_add_rollup_delta_events
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "012_add_alert_tables"
down_revision = "011_add_rollup_delta_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "alert_rules" not in tables:
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("metric", sa.String(length=48), nullable=False),
            sa.Column("comparator", sa.String(length=8), nullable=False, server_default=">="),
            sa.Column("threshold", sa.Float(), nullable=False, server_default="95.0"),
            sa.Column("window_s", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="warning"),
            sa.Column("cooldown_s", sa.Integer(), nullable=False, server_default="300"),
            sa.Column("target_kind", sa.String(length=16), nullable=False, server_default="gateway"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("channels", sa.JSON(), nullable=False, server_default='["webhook"]'),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_alert_rules_metric", "alert_rules", ["metric"])

    if "alert_incidents" not in tables:
        op.create_table(
            "alert_incidents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("rule_id", sa.String(length=64), nullable=False),
            sa.Column("rule_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("metric", sa.String(length=48), nullable=False, server_default=""),
            sa.Column("target", sa.String(length=255), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="warning"),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="firing"),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("ack_by", sa.String(length=128), nullable=True),
            sa.Column("ack_at", sa.DateTime(), nullable=True),
            sa.Column("ack_note", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("value", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("notified_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_alert_incidents_rule_id", "alert_incidents", ["rule_id"])
        op.create_index("ix_alert_incidents_state", "alert_incidents", ["state"])
        op.create_index("ix_alert_incidents_rule_target", "alert_incidents", ["rule_id", "target"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "alert_incidents" in tables:
        op.drop_table("alert_incidents")
    if "alert_rules" in tables:
        op.drop_table("alert_rules")
