"""Add export_records — one row per invoice put through the service

Revision ID: 0004_export_history
Revises: 0003_staff_flag
Create Date: 2026-09-10 07:17:30.771423
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0004_export_history'
down_revision = '0003_staff_flag'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('export_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('filename', sa.String(length=256), nullable=True),
    sa.Column('fmt', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('vendor_identifier', sa.String(length=64), nullable=True),
    sa.Column('vendor_name', sa.String(length=256), nullable=True),
    sa.Column('invoice_no', sa.String(length=128), nullable=True),
    sa.Column('located', sa.Integer(), nullable=False),
    sa.Column('requested', sa.Integer(), nullable=False),
    sa.Column('missing', sa.JSON(), nullable=True),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('problems', sa.Integer(), nullable=False),
    sa.Column('ocr_used', sa.Boolean(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('export_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_export_records_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_export_records_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_export_records_vendor_identifier'), ['vendor_identifier'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('export_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_export_records_vendor_identifier'))
        batch_op.drop_index(batch_op.f('ix_export_records_user_id'))
        batch_op.drop_index(batch_op.f('ix_export_records_created_at'))

    op.drop_table('export_records')
