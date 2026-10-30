"""Add obs_integration table for UI-managed observability integrations (v0.14.2)

Revision ID: 007_obs_integration
Revises: 006_gpu_hosts
Create Date: 2026-10-30

Idempotent like 004/006: checks the live schema first so a create_all that already
made the table does not make this fail.
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect

revision = "007_obs_integration"
down_revision = "006_gpu_hosts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "obs_integration" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "obs_integration",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("secrets_enc", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    if "obs_integration" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("obs_integration")
