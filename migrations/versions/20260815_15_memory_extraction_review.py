"""Add boundary extraction audit, profile drafts, and memory lifecycle fields.

Revision ID: 20260815_15
Revises: 20260815_14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_15"
down_revision = "20260815_14"
branch_labels = None
depends_on = None


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def _ensure_indexes(table: str, columns: tuple[str, ...]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for column in columns:
        name = f"ix_{table}_{column}"
        if name not in existing:
            op.create_index(name, table, [column])


def upgrade() -> None:
    _add_columns(
        "memory_candidates",
        [
            sa.Column("operation", sa.String(20), nullable=False, server_default="ADD"),
            sa.Column("existing_memory_id", sa.String(36), nullable=True),
            sa.Column("source_conversation_id", sa.String(36), nullable=True),
            sa.Column("source_message_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("extraction_run_id", sa.String(36), nullable=True),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("raw_temporal_expression", sa.String(200), nullable=True),
        ],
    )
    _add_columns(
        "memories",
        [
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("supersedes_memory_id", sa.String(36), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_conversation_id", sa.String(36), nullable=True),
            sa.Column("source_message_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("retrieval_index_status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("retrieval_index_error", sa.Text(), nullable=True),
        ],
    )
    _ensure_indexes(
        "memory_candidates",
        ("operation", "existing_memory_id", "source_conversation_id", "extraction_run_id"),
    )
    _ensure_indexes(
        "memories",
        ("active", "supersedes_memory_id", "source_conversation_id", "retrieval_index_status"),
    )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "memory_extraction_runs" not in tables:
        op.create_table(
            "memory_extraction_runs",
            sa.Column("extraction_run_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
            sa.Column("start_watermark_message_id", sa.String(36), nullable=True),
            sa.Column("end_boundary_message_id", sa.String(36), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("input_mode", sa.String(30), nullable=True),
            sa.Column("input_token_count", sa.Integer(), nullable=True),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("conversation_id", "start_watermark_message_id", "end_boundary_message_id", name="uq_memory_extraction_segment"),
        )
    _ensure_indexes("memory_extraction_runs", ("user_id", "conversation_id", "end_boundary_message_id", "status", "created_at"))
    if "profile_revision_drafts" not in tables:
        op.create_table(
            "profile_revision_drafts",
            sa.Column("draft_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_type", sa.String(50), nullable=False),
            sa.Column("source_conversation_id", sa.String(36), nullable=True),
            sa.Column("source_message_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        )
    _ensure_indexes("profile_revision_drafts", ("user_id", "source_conversation_id", "status", "created_at"))
    if "profile_revision_changes" not in tables:
        op.create_table(
            "profile_revision_changes",
            sa.Column("change_id", sa.String(36), primary_key=True),
            sa.Column("draft_id", sa.String(36), sa.ForeignKey("profile_revision_drafts.draft_id", ondelete="CASCADE"), nullable=False),
            sa.Column("field_key", sa.String(100), nullable=False),
            sa.Column("operation", sa.String(20), nullable=False),
            sa.Column("before_value", sa.JSON(), nullable=True),
            sa.Column("proposed_value", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("source_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_indexes("profile_revision_changes", ("draft_id", "field_key", "status"))


def downgrade() -> None:
    op.drop_table("profile_revision_changes")
    op.drop_table("profile_revision_drafts")
    op.drop_table("memory_extraction_runs")
