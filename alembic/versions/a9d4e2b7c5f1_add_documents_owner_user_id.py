"""add documents.owner_user_id (per-user document scoping)

Revision ID: a9d4e2b7c5f1
Revises: e5a8f3c1d9b6
Create Date: 2026-10-29 18:30:00.000000

Uploads become private to the account that made them: each document row
records its owning user. NULL means shared corpus (CLI/script ingestion,
pre-identity uploads) and stays visible to every signed-in user. Deleting
an account keeps the corpus (SET NULL) instead of cascading deletes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9d4e2b7c5f1'
down_revision: Union[str, Sequence[str], None] = 'e5a8f3c1d9b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_owner_user_id",
        "documents",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_owner_user_id", "documents", ["owner_user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_documents_owner_user_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_owner_user_id", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "owner_user_id")
