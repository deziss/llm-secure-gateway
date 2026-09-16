"""v0.1 to v0.3 - Add auth, owners, permissions, settings, invites

Revision ID: 001_v01_to_v03
Revises: (initial)
Create Date: 2026-03-09

This migration upgrades the database schema from v0.1 (basic LLM backend + API key management)
to v0.3 (full RBAC auth, owner permissions, system settings, invite codes).

Changes:
  - Add `user` table (FastAPI Users auth with roles)
  - Add `owner` table (API resource owners)
  - Add `providerkey` table (encrypted provider API keys per owner)
  - Add `ownerpermission` table (granular backend/model permissions)
  - Add `systemsetting` table (key/value configuration store)
  - Add `invitecode` table (registration invite system)
  - Add `accesstoken` table (FastAPI Users token storage)
  - Add columns to `llmbackend`: fallback_urls, allowed_endpoints
  - Add columns to `apikey`: updated_at
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_v01_to_v03'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- User table (FastAPI Users) ---
    op.create_table(
        'user',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('email', sa.String(length=320), nullable=False, unique=True, index=True),
        sa.Column('hashed_password', sa.String(length=1024), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('role', sa.String(), nullable=False, server_default='viewer'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
    )

    # --- Access Token table (FastAPI Users) ---
    op.create_table(
        'accesstoken',
        sa.Column('token', sa.String(length=43), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True),
    )

    # --- Owner table ---
    op.create_table(
        'owner',
        sa.Column('id', sa.String(), primary_key=True, index=True),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('user_id', sa.String(), nullable=True, index=True),
        sa.Column('block_endpoints', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # --- Provider Key table ---
    op.create_table(
        'providerkey',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('owner_id', sa.String(), sa.ForeignKey('owner.id'), nullable=False, index=True),
        sa.Column('provider_id', sa.String(), nullable=False, index=True),
        sa.Column('encrypted_key', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('owner_id', 'provider_id', name='uq_owner_provider'),
    )

    # --- Owner Permission table ---
    op.create_table(
        'ownerpermission',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('owner_id', sa.String(), sa.ForeignKey('owner.id'), nullable=False, index=True),
        sa.Column('backend_name', sa.String(), sa.ForeignKey('llmbackend.name'), nullable=False, index=True),
        sa.Column('allowed_models', sa.JSON(), nullable=True),
        sa.Column('allowed_endpoints', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    # --- System Setting table ---
    op.create_table(
        'systemsetting',
        sa.Column('key', sa.String(), primary_key=True),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # --- Invite Code table ---
    op.create_table(
        'invitecode',
        sa.Column('code', sa.String(), primary_key=True),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('used_by', sa.String(), nullable=True),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    # --- Add new columns to existing llmbackend table ---
    # These columns may already exist if the table was created with v0.3 models.
    # Use batch_alter_table for safety.
    try:
        op.add_column('llmbackend', sa.Column('fallback_urls', sa.JSON(), nullable=True))
    except Exception:
        pass  # Column may already exist

    try:
        op.add_column('llmbackend', sa.Column('allowed_endpoints', sa.JSON(), nullable=True))
    except Exception:
        pass  # Column may already exist

    try:
        op.add_column('apikey', sa.Column('updated_at', sa.DateTime(), nullable=True))
    except Exception:
        pass  # Column may already exist


def downgrade() -> None:
    # Drop new tables
    op.drop_table('invitecode')
    op.drop_table('systemsetting')
    op.drop_table('ownerpermission')
    op.drop_table('providerkey')
    op.drop_table('owner')
    op.drop_table('accesstoken')
    op.drop_table('user')

    # Remove added columns
    try:
        op.drop_column('llmbackend', 'fallback_urls')
    except Exception:
        pass
    try:
        op.drop_column('llmbackend', 'allowed_endpoints')
    except Exception:
        pass
    try:
        op.drop_column('apikey', 'updated_at')
    except Exception:
        pass
