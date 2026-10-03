"""Rename table llmbackend -> model_server ("backends" are now "model servers")

Revision ID: 005_model_server
Revises: 004_roadmap_v060
Create Date: 2026-10-03

PostgreSQL foreign keys follow a renamed table automatically (they reference
it by OID), so ownerpermission/modelalias/llmbot keep working. Column names
such as backend_name and backend_type are unchanged.

Safety: the gateway runs SQLModel.create_all on startup. If new code ever
starts before this migration, create_all makes an *empty* model_server
table next to the real llmbackend one. That empty table is dropped here
before the rename; a non-empty one aborts the migration rather than lose data.
"""

from alembic import op
from sqlalchemy import inspect, text

revision = "005_model_server"
down_revision = "004_roadmap_v060"
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names())


def _rename_index(old: str, new: str) -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    exists = conn.execute(text("SELECT 1 FROM pg_class WHERE relname = :n"), {"n": old}).scalar()
    taken = conn.execute(text("SELECT 1 FROM pg_class WHERE relname = :n"), {"n": new}).scalar()
    if exists and not taken:
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    tables = _tables()
    if "llmbackend" not in tables:
        return  # already renamed (or created fresh as model_server)
    if "model_server" in tables:
        rows = op.get_bind().execute(text("SELECT count(*) FROM model_server")).scalar()
        if rows:
            raise RuntimeError(
                "Both llmbackend and a non-empty model_server table exist; "
                "refusing to guess which is current. Merge them manually."
            )
        op.drop_table("model_server")
    op.rename_table("llmbackend", "model_server")
    _rename_index("llmbackend_pkey", "model_server_pkey")
    _rename_index("ix_llmbackend_name", "ix_model_server_name")


def downgrade() -> None:
    if "model_server" in _tables() and "llmbackend" not in _tables():
        op.rename_table("model_server", "llmbackend")
        _rename_index("model_server_pkey", "llmbackend_pkey")
        _rename_index("ix_model_server_name", "ix_llmbackend_name")
