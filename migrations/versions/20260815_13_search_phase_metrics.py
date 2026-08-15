"""Add privacy-safe internal search phase metrics.

Revision ID: 20260815_13
Revises: 20260815_12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_13"
down_revision = "20260815_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "search_phase_metrics" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "search_phase_metrics",
            sa.Column("search_phase_metric_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True),
            sa.Column("search_session_id", sa.String(36), sa.ForeignKey("search_sessions.search_session_id", ondelete="SET NULL"), nullable=True),
            sa.Column("phase", sa.String(100), nullable=False),
            sa.Column("provider", sa.String(100), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("candidate_count", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("timed_out", sa.Boolean(), nullable=False),
            sa.Column("embedding_count", sa.Integer(), nullable=False),
            sa.Column("embedding_cache_hit_count", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    existing = {item["name"] for item in sa.inspect(bind).get_indexes("search_phase_metrics")}
    for column in ("user_id", "run_id", "search_session_id", "phase", "created_at"):
        name = f"ix_search_phase_metrics_{column}"
        if name not in existing:
            op.create_index(name, "search_phase_metrics", [column])


def downgrade() -> None:
    op.drop_table("search_phase_metrics")
