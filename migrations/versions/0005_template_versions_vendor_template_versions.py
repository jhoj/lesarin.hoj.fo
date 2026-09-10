"""Add vendor_template_versions — history and rollback for shared templates

Revision ID: 0005_template_versions
Revises: 0004_export_history
Create Date: 2026-09-10 07:23:15.931138
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0005_template_versions'
down_revision = '0004_export_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('vendor_template_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('identifier', sa.String(length=64), nullable=False),
    sa.Column('identifier_kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('match_keywords', sa.JSON(), nullable=True),
    sa.Column('mappings', sa.JSON(), nullable=False),
    sa.Column('change', sa.String(length=16), nullable=False),
    sa.Column('changed_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('vendor_template_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vendor_template_versions_identifier'), ['identifier'], unique=False)
        batch_op.create_index(batch_op.f('ix_vendor_template_versions_vendor_id'), ['vendor_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('vendor_template_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendor_template_versions_vendor_id'))
        batch_op.drop_index(batch_op.f('ix_vendor_template_versions_identifier'))

    op.drop_table('vendor_template_versions')
