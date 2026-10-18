"""Add documents.content_hash for idempotent ingestion (dedup).

Revision ID: d4f8a2c1e6b7
Revises: bbfc9285daca
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "d4f8a2c1e6b7"
down_revision = "bbfc9285daca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_column("documents", "content_hash")
