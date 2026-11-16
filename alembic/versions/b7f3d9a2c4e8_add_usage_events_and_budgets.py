"""add usage_events and per-user budget overrides (WS-5 cost governance)

Revision ID: b7f3d9a2c4e8
Revises: a9d4e2b7c5f1
Create Date: 2026-10-30 18:30:00.000000

usage_events: one row per token-spending request (chat/retrieve), written
fail-open. user_id is SET NULL on delete so the spend ledger outlives
accounts; est_usd NULL means "model price unknown", never zero.

users gains nullable per-user budget overrides; NULL = fall back to the
global USAGE_*_BUDGET_USD settings.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f3d9a2c4e8'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2b7c5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'usage_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('endpoint', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('est_usd', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        'fk_usage_events_user_id', 'usage_events', 'users',
        ['user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_usage_events_user_id', 'usage_events', ['user_id'], unique=False)
    op.create_index('ix_usage_events_endpoint', 'usage_events', ['endpoint'], unique=False)
    op.create_index('ix_usage_events_created_at', 'usage_events', ['created_at'], unique=False)

    # Per-user budget overrides (NULL = global default applies).
    op.add_column('users', sa.Column('daily_budget_usd', sa.Float(), nullable=True))
    op.add_column('users', sa.Column('monthly_budget_usd', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'monthly_budget_usd')
    op.drop_column('users', 'daily_budget_usd')
    op.drop_index('ix_usage_events_created_at', table_name='usage_events')
    op.drop_index('ix_usage_events_endpoint', table_name='usage_events')
    op.drop_index('ix_usage_events_user_id', table_name='usage_events')
    op.drop_constraint('fk_usage_events_user_id', 'usage_events', type_='foreignkey')
    op.drop_table('usage_events')
