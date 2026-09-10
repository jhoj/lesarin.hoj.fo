"""Add API keys, TOTP credentials, and the login-safety columns on users

Revision ID: 0002_auth_hardening
Revises: 0001_baseline
Create Date: 2026-09-10 22:05:03.129230
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002_auth_hardening'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('hashed_key', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_prefix'), ['prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_keys_user_id'), ['user_id'], unique=False)

    op.create_table('mfa_credentials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('secret', sa.String(length=64), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('recovery_codes', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mfa_credentials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mfa_credentials_user_id'), ['user_id'], unique=True)

    with op.batch_alter_table('users', schema=None) as batch_op:
        # server_default, or these fail on any users table that already has
        # rows — same defaults the hand-rolled _ensure_columns() used before
        # migrations existed.
        batch_op.add_column(
            sa.Column('token_version', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_count')
        batch_op.drop_column('token_version')

    with op.batch_alter_table('mfa_credentials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mfa_credentials_user_id'))

    op.drop_table('mfa_credentials')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_user_id'))
        batch_op.drop_index(batch_op.f('ix_api_keys_prefix'))

    op.drop_table('api_keys')
