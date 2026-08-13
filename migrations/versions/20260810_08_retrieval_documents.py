"""Add the unified hybrid-retrieval document corpus.

Revision ID: 20260810_08
Revises: 20260810_07
"""

from alembic import op
import sqlalchemy as sa

from app.database.types import PortableVector


revision = "20260810_08"
down_revision = "20260810_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "retrieval_documents" not in set(inspector.get_table_names()):
        op.create_table(
            "retrieval_documents",
            sa.Column("retrieval_document_id", sa.String(36), primary_key=True),
            sa.Column("corpus_type", sa.String(50), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True),
            sa.Column("source_entity_id", sa.String(100), nullable=False),
            sa.Column("source_version", sa.String(100), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("search_vector", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("embedding_model_id", sa.String(200), nullable=True),
            sa.Column("embedding_dimension", sa.Integer(), nullable=True),
            sa.Column("embedding", PortableVector(1024), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("corpus_type", "user_id", "source_entity_id", "source_version", "content_hash", name="uq_retrieval_document_source_version_hash"),
        )
        for column in ("corpus_type", "user_id", "source_entity_id", "content_hash", "retrieved_at", "expires_at"):
            op.create_index(f"ix_retrieval_documents_{column}", "retrieval_documents", [column])

    if bind.dialect.name == "cockroachdb":
        index_names = {item["name"] for item in sa.inspect(bind).get_indexes("retrieval_documents")}
        column_names = {item["name"] for item in sa.inspect(bind).get_columns("retrieval_documents")}
        if "search_vector_fts" not in column_names:
            op.execute(
                "ALTER TABLE retrieval_documents ADD COLUMN search_vector_fts TSVECTOR "
                "AS (to_tsvector('english', coalesce(title, '') || ' ' || text)) STORED"
            )
        if "ix_retrieval_documents_fts" not in index_names:
            op.execute(
                "CREATE INVERTED INDEX ix_retrieval_documents_fts "
                "ON retrieval_documents (search_vector_fts)"
            )
        if "ix_retrieval_documents_embedding" not in index_names:
            op.execute("CREATE VECTOR INDEX ix_retrieval_documents_embedding ON retrieval_documents (embedding vector_cosine_ops)")

    if "retrieval_query_logs" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "retrieval_query_logs",
            sa.Column("retrieval_query_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("corpus_types", sa.JSON(), nullable=False),
            sa.Column("rankings_json", sa.JSON(), nullable=False),
            sa.Column("warnings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_retrieval_query_logs_user_id", "retrieval_query_logs", ["user_id"])
        op.create_index("ix_retrieval_query_logs_created_at", "retrieval_query_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("retrieval_query_logs")
    op.drop_table("retrieval_documents")
