"""Add translation_mode and normalize_thinking to llmbackend

Revision ID: 002_add_translation_mode
Revises: 001_baseline_v051
Create Date: 2026-05-21

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# --------------------------------------------------------------------------- #
# Alembic identifiers
# --------------------------------------------------------------------------- #
revision = "002_add_translation_mode"
down_revision = "001_baseline_v051"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Add translation_mode and normalize_thinking to llmbackend table
    # ------------------------------------------------------------------ #
    conn = op.get_bind()
    insp = inspect(conn)
    
    if "llmbackend" in insp.get_table_names():
        if not _column_exists("llmbackend", "translation_mode"):
            op.add_column(
                "llmbackend",
                sa.Column("translation_mode", sa.String(), nullable=False, server_default="none")
            )
        if not _column_exists("llmbackend", "normalize_thinking"):
            op.add_column(
                "llmbackend",
                sa.Column("normalize_thinking", sa.Boolean(), nullable=False, server_default="false")
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    
    if "llmbackend" in insp.get_table_names():
        if _column_exists("llmbackend", "translation_mode"):
            op.drop_column("llmbackend", "translation_mode")
        if _column_exists("llmbackend", "normalize_thinking"):
            op.drop_column("llmbackend", "normalize_thinking")
