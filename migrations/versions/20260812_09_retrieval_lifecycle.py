"""Add deterministic retrieval lifecycle state.

Revision ID: 20260812_09
Revises: 20260810_08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_09"
down_revision = "20260810_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("retrieval_documents")}
    if "active" not in columns:
        with op.batch_alter_table("retrieval_documents") as batch:
            batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
            batch.create_index("ix_retrieval_documents_active", ["active"])
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("retrieval_documents")}
    if "ix_retrieval_documents_visibility" not in indexes:
        op.create_index(
            "ix_retrieval_documents_visibility",
            "retrieval_documents",
            ["user_id", "corpus_type", "active", "expires_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("retrieval_documents") as batch:
        batch.drop_index("ix_retrieval_documents_visibility")
        batch.drop_index("ix_retrieval_documents_active")
        batch.drop_column("active")
