"""Governance content capture: input_text/output_text/capture_level on agent_steps.

Revision ID: 016_capture_columns
Revises: 015_alert_rule_customise
Create Date: 2026-12-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "016_capture_columns"
down_revision = "015_alert_rule_customise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("agent_steps")}
    if "input_text" not in cols:
        op.add_column("agent_steps", sa.Column("input_text", sa.Text(), nullable=True))
    if "output_text" not in cols:
        op.add_column("agent_steps", sa.Column("output_text", sa.Text(), nullable=True))
    if "capture_level" not in cols:
        op.add_column("agent_steps", sa.Column("capture_level", sa.String(length=16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("agent_steps")}
    for name in ("capture_level", "output_text", "input_text"):
        if name in cols:
            op.drop_column("agent_steps", name)
