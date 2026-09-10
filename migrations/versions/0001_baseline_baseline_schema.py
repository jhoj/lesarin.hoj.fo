"""Baseline: the schema as it stood before Alembic.

Generated from the models so it matches what create_all() used to build. A
database that predates Alembic is stamped with this revision rather than
running it — see app/db.py:run_migrations.

Revision ID: 0001_baseline
Revises: None
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('output_fields',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('display_name', sa.String(length=256), nullable=False),
    sa.Column('value_type', sa.String(length=16), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('vendors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('identifier', sa.String(length=64), nullable=False),
    sa.Column('identifier_kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('match_keywords', sa.JSON(), nullable=True),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identifier', 'identifier_kind', name='uq_vendor_identifier')
    )
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vendors_identifier'), ['identifier'], unique=False)

    op.create_table('field_mappings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=False),
    sa.Column('output_key', sa.String(length=128), nullable=False),
    sa.Column('strategy', sa.String(length=16), nullable=False),
    sa.Column('source_label', sa.String(length=256), nullable=True),
    sa.Column('relation', sa.String(length=16), nullable=False),
    sa.Column('value_type', sa.String(length=16), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('x0', sa.Float(), nullable=True),
    sa.Column('top', sa.Float(), nullable=True),
    sa.Column('x1', sa.Float(), nullable=True),
    sa.Column('bottom', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('output_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('fmt', sa.String(length=16), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('output_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_output_profiles_user_id'), ['user_id'], unique=False)

    op.create_table('profile_fields',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('canonical', sa.String(length=64), nullable=False),
    sa.Column('output_name', sa.String(length=128), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['output_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('profile_fields', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_profile_fields_profile_id'), ['profile_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('profile_fields', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_profile_fields_profile_id'))

    op.drop_table('profile_fields')
    with op.batch_alter_table('output_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_output_profiles_user_id'))

    op.drop_table('output_profiles')
    op.drop_table('field_mappings')
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendors_identifier'))

    op.drop_table('vendors')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    op.drop_table('output_fields')
