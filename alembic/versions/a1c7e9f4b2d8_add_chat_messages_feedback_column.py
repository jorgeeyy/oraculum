"""add chat_messages.feedback column

Revision ID: a1c7e9f4b2d8
Revises: f3a9c2d8e1b4
Create Date: 2026-10-27 12:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c7e9f4b2d8'
down_revision: Union[str, Sequence[str], None] = 'f3a9c2d8e1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'chat_messages',
        sa.Column('feedback', sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chat_messages', 'feedback')
