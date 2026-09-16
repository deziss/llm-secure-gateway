"""owner_schema_improvements

Revision ID: 002
Revises: 001
Create Date: 2026-03-11

- Add is_active, description, max_keys columns to owner table
- Rename apikey.owner -> apikey.owner_id and add FK constraint
- Add unique constraint on ownerpermission(owner_id, backend_name)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001_v01_to_v03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Owner table: add new columns ---
    op.add_column('owner', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('owner', sa.Column('description', sa.String(), nullable=True))
    op.add_column('owner', sa.Column('max_keys', sa.Integer(), nullable=False, server_default=sa.text('5')))

    # --- APIKey table: rename owner -> owner_id and add FK ---
    op.alter_column('apikey', 'owner', new_column_name='owner_id')
    op.create_foreign_key(
        'fk_apikey_owner_id',
        'apikey', 'owner',
        ['owner_id'], ['id'],
        ondelete='CASCADE'
    )

    # --- OwnerPermission: add unique constraint ---
    op.create_unique_constraint(
        'uq_owner_backend_perm',
        'ownerpermission',
        ['owner_id', 'backend_name']
    )


def downgrade() -> None:
    # --- OwnerPermission: drop unique constraint ---
    op.drop_constraint('uq_owner_backend_perm', 'ownerpermission', type_='unique')

    # --- APIKey: drop FK, rename back ---
    op.drop_constraint('fk_apikey_owner_id', 'apikey', type_='foreignkey')
    op.alter_column('apikey', 'owner_id', new_column_name='owner')

    # --- Owner: drop new columns ---
    op.drop_column('owner', 'max_keys')
    op.drop_column('owner', 'description')
    op.drop_column('owner', 'is_active')
