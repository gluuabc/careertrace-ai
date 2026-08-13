"""Add privacy-preserving model call metrics.

Revision ID: 20260812_10
Revises: 20260812_09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_10"
down_revision = "20260812_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "model_call_metrics" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "model_call_metrics",
        sa.Column("model_call_metric_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(300), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("rough_estimated_input_tokens", sa.Integer(), nullable=True),
        sa.Column("preflight_input_tokens", sa.Integer(), nullable=True),
        sa.Column("preflight_count_source", sa.String(50), nullable=True),
        sa.Column("actual_input_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_output_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_total_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_input_tokens", sa.Integer(), nullable=True),
        sa.Column("model_context_limit", sa.Integer(), nullable=True),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=True),
        sa.Column("safety_margin_tokens", sa.Integer(), nullable=True),
        sa.Column("compression_threshold", sa.Integer(), nullable=True),
        sa.Column("compression_triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.String(200), nullable=True),
        sa.Column("error_type", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "conversation_id", "run_id", "stage", "created_at"):
        op.create_index(f"ix_model_call_metrics_{column}", "model_call_metrics", [column])


def downgrade() -> None:
    op.drop_table("model_call_metrics")
