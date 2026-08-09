"""Add durable search sessions and per-source progress.

Revision ID: 20260810_07
Revises: 20260809_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_07"
down_revision = "20260809_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "search_sessions" not in tables:
        op.create_table(
        "search_sessions",
        sa.Column("search_session_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("intent", sa.String(50), nullable=False),
        sa.Column("normalized_request", sa.JSON(), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("source_call_budget", sa.Integer(), nullable=False),
        sa.Column("source_calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remaining_source_budget", sa.Integer(), nullable=False),
        sa.Column("consecutive_no_progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visited_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("provider_cursors", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("seen_candidate_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("candidate_records", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("query_variants", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_failures", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_coverage", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("run_id", "user_id", "intent", "status"):
            op.create_index(f"ix_search_sessions_{column}", "search_sessions", [column])

    if "search_source_progress" not in tables:
        op.create_table(
        "search_source_progress",
        sa.Column("source_progress_id", sa.String(36), primary_key=True),
        sa.Column("search_session_id", sa.String(36), sa.ForeignKey("search_sessions.search_session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("company_or_domain", sa.String(500), nullable=True),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("visited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("next_cursor", sa.Text(), nullable=True),
        sa.Column("has_more", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exhausted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_iteration", sa.Integer(), nullable=True),
        sa.Column("last_iteration", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(100), nullable=True),
        sa.UniqueConstraint("search_session_id", "source_key", name="uq_search_source_session_key"),
        )
        op.create_index("ix_search_source_progress_search_session_id", "search_source_progress", ["search_session_id"])
        op.create_index("ix_search_source_progress_provider", "search_source_progress", ["provider"])


def downgrade() -> None:
    op.drop_table("search_source_progress")
    op.drop_table("search_sessions")
