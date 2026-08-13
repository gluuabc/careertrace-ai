"""Correct Cockroach retrieval indexes and visibility scope.

Revision ID: 20260813_11
Revises: 20260812_10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_11"
down_revision = "20260812_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("retrieval_documents")}
    if "ix_retrieval_documents_visibility" not in indexes:
        op.create_index(
            "ix_retrieval_documents_visibility",
            "retrieval_documents",
            ["user_id", "corpus_type", "active", "expires_at"],
        )
    if bind.dialect.name != "cockroachdb":
        return

    columns = {item["name"] for item in sa.inspect(bind).get_columns("retrieval_documents")}
    if "search_vector_fts" not in columns:
        op.execute(
            "ALTER TABLE retrieval_documents ADD COLUMN search_vector_fts TSVECTOR "
            "AS (to_tsvector('english', coalesce(title, '') || ' ' || text)) STORED"
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("retrieval_documents")}
    if "ix_retrieval_documents_fts" not in indexes:
        op.execute(
            "CREATE INVERTED INDEX ix_retrieval_documents_fts "
            "ON retrieval_documents (search_vector_fts)"
        )
    if "ix_retrieval_documents_embedding" not in indexes:
        op.execute(
            "CREATE VECTOR INDEX ix_retrieval_documents_embedding "
            "ON retrieval_documents (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("retrieval_documents")}
    if "ix_retrieval_documents_visibility" in indexes:
        op.drop_index("ix_retrieval_documents_visibility", table_name="retrieval_documents")
    if bind.dialect.name == "cockroachdb":
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes("retrieval_documents")}
        if "ix_retrieval_documents_fts" in indexes:
            op.drop_index("ix_retrieval_documents_fts", table_name="retrieval_documents")
        columns = {item["name"] for item in sa.inspect(bind).get_columns("retrieval_documents")}
        if "search_vector_fts" in columns:
            op.drop_column("retrieval_documents", "search_vector_fts")
