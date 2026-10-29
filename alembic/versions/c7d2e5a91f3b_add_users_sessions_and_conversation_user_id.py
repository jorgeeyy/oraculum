"""add users, sessions, and conversations.user_id (identity)

Revision ID: c7d2e5a91f3b
Revises: a1c7e9f4b2d8
Create Date: 2026-10-29 10:00:00.000000

Backfill is intentionally NOT done here: conversations.user_id is adopted
in application code once the admin account exists (first registration, the
API-key service account's first persist, or scripts/create_admin.py), so no
migration needs to read .env to guess who the current owner is.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d2e5a91f3b'
down_revision: Union[str, Sequence[str], None] = 'a1c7e9f4b2d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=True),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.create_table(
        'sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
    )
    op.create_index('ix_sessions_token_hash', 'sessions', ['token_hash'], unique=True)
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'], unique=False)
    op.create_index('ix_sessions_expires_at', 'sessions', ['expires_at'], unique=False)

    op.add_column(
        'conversations',
        sa.Column('user_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_conversations_user_id', 'conversations', 'users',
        ['user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    op.drop_constraint('fk_conversations_user_id', 'conversations', type_='foreignkey')
    op.drop_column('conversations', 'user_id')
    op.drop_index('ix_sessions_expires_at', table_name='sessions')
    op.drop_index('ix_sessions_user_id', table_name='sessions')
    op.drop_index('ix_sessions_token_hash', table_name='sessions')
    op.drop_table('sessions')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
