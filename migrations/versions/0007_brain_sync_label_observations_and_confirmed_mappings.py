"""Add field_mappings.confirmed and label_observations — the brain-sync plane

Revision ID: 0007_brain_sync
Revises: 0006_password_reset
Create Date: 2026-09-11 09:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0007_brain_sync'
down_revision = '0006_password_reset'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('field_mappings', schema=None) as batch_op:
        # server_default matters: existing mappings predate the confirmed
        # concept and were never reviewed field-by-field, so they default to
        # unconfirmed (a local convenience, not eligible to push centrally).
        batch_op.add_column(
            sa.Column('confirmed', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table('label_observations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('identifier', sa.String(length=64), nullable=True),
    sa.Column('layout_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('label_set', sa.JSON(), nullable=False),
    sa.Column('positions', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('label_observations')

    with op.batch_alter_table('field_mappings', schema=None) as batch_op:
        batch_op.drop_column('confirmed')
