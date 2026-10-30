"""add audit_events (admin action trail)

Revision ID: e5a8f3c1d9b6
Revises: c7d2e5a91f3b
Create Date: 2026-10-29 14:00:00.000000

Append-only trail for admin/security-relevant actions (role changes,
password resets, later: doc deletes, budget changes). Actor FK is
SET NULL on user delete; actor_label keeps the human-readable identity.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a8f3c1d9b6'
down_revision: Union[str, Sequence[str], None] = 'c7d2e5a91f3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('actor_user_id', sa.Uuid(), nullable=True),
        sa.Column('actor_label', sa.String(length=320), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=64), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        'fk_audit_events_actor_user_id', 'audit_events', 'users',
        ['actor_user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_audit_events_actor_user_id', 'audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_audit_events_action', 'audit_events', ['action'], unique=False)
    op.create_index('ix_audit_events_created_at', 'audit_events', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_events_created_at', table_name='audit_events')
    op.drop_index('ix_audit_events_action', table_name='audit_events')
    op.drop_index('ix_audit_events_actor_user_id', table_name='audit_events')
    op.drop_constraint('fk_audit_events_actor_user_id', 'audit_events', type_='foreignkey')
    op.drop_table('audit_events')
