"""Semantic prompt-validation exemplars.

Adds the ``promptexemplar`` table backing Know Your Prompts / Know Your
Vectors semantic prompt validation (bad/good exemplars compared at request
time with in-process sparse n-gram cosine similarity).

Revision ID: 019_prompt_exemplars
Revises: 018_key_rotation
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa

revision = "019_prompt_exemplars"
down_revision = "018_key_rotation"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "promptexemplar"):
        return
    op.create_table(
        "promptexemplar",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promptexemplar_kind", "promptexemplar", ["kind"])
    op.create_index("ix_promptexemplar_is_active", "promptexemplar", ["is_active"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "promptexemplar"):
        return
    op.drop_index("ix_promptexemplar_is_active", table_name="promptexemplar")
    op.drop_index("ix_promptexemplar_kind", table_name="promptexemplar")
    op.drop_table("promptexemplar")
