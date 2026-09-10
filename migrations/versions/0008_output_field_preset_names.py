"""Add output_fields.preset_names

Revision ID: 0008_output_field_preset_names
Revises: 0007_brain_sync
Create Date: 2026-09-11 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0008_output_field_preset_names'
down_revision = '0007_brain_sync'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('output_fields', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preset_names', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('output_fields', schema=None) as batch_op:
        batch_op.drop_column('preset_names')
