"""Alert rule customisation: target_filter + description columns.

Revision ID: 015_alert_rule_customise
Revises: 014_add_agent_tables
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "015_alert_rule_customise"
down_revision = "014_add_agent_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("alert_rules")}
    if "target_filter" not in cols:
        op.add_column("alert_rules", sa.Column("target_filter", sa.JSON(), nullable=True))
        op.execute("UPDATE alert_rules SET target_filter = '[]' WHERE target_filter IS NULL")
    if "description" not in cols:
        op.add_column("alert_rules", sa.Column("description", sa.String(length=500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("alert_rules")}
    if "description" in cols:
        op.drop_column("alert_rules", "description")
    if "target_filter" in cols:
        op.drop_column("alert_rules", "target_filter")
