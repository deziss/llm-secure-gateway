"""Add gpu_host table and model_server.gpu_host / gpu_indices (observability v0.14.0)

Revision ID: 006_gpu_hosts
Revises: 005_model_server
Create Date: 2026-10-29

Idempotent: every step checks the live schema first, the same way
004_roadmap_v060 does, so re-running after a create_all is safe.
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect

revision = "006_gpu_hosts"
down_revision = "005_model_server"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())

    if "gpu_host" not in tables:
        op.create_table(
            "gpu_host",
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("name"),
        )
        op.create_index(op.f("ix_gpu_host_name"), "gpu_host", ["name"], unique=False)

    if "model_server" in tables:
        cols = _columns("model_server")
        if "gpu_host" not in cols:
            op.add_column("model_server", sa.Column("gpu_host", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        if "gpu_indices" not in cols:
            op.add_column("model_server", sa.Column("gpu_indices", sa.JSON(), nullable=True))


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "model_server" in tables:
        cols = _columns("model_server")
        with op.batch_alter_table("model_server") as batch:
            if "gpu_indices" in cols:
                batch.drop_column("gpu_indices")
            if "gpu_host" in cols:
                batch.drop_column("gpu_host")
    if "gpu_host" in tables:
        op.drop_index(op.f("ix_gpu_host_name"), table_name="gpu_host")
        op.drop_table("gpu_host")
