"""Add users.is_staff — the customer/staff line for the shared brain

Revision ID: 0002_staff_flag
Revises: 0001_baseline
Create Date: 2026-09-10 07:12:22.650744
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002_staff_flag'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        # server_default matters: existing accounts already have rows, and a
        # NOT NULL column without one fails on any non-empty users table.
        # Everyone starts as a customer; staff are promoted explicitly.
        batch_op.add_column(
            sa.Column('is_staff', sa.Boolean(), nullable=False, server_default=sa.false())
        )



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_staff')

