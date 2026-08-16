"""Add conversation-scoped memory signals and extraction pending state.

Revision ID: 20260815_14
Revises: 20260815_13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_14"
down_revision = "20260815_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "conversation_memory_signals" not in tables:
        op.create_table(
            "conversation_memory_signals",
            sa.Column("signal_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_message_id", sa.String(36), sa.ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False),
            sa.Column("signal_index", sa.Integer(), nullable=False),
            sa.Column("signal_type", sa.String(100), nullable=False),
            sa.Column("operation_hint", sa.String(30), nullable=False),
            sa.Column("value_hint", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("source_message_id", "signal_index", name="uq_conversation_signal_message_index"),
        )
    signal_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("conversation_memory_signals")}
    for column in ("user_id", "conversation_id", "source_message_id", "signal_type", "created_at"):
        name = f"ix_conversation_memory_signals_{column}"
        if name not in signal_indexes:
            op.create_index(name, "conversation_memory_signals", [column])
    if "conversation_memory_states" not in tables:
        op.create_table(
            "conversation_memory_states",
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("last_memory_extraction_message_id", sa.String(36), sa.ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True),
            sa.Column("pending_boundary_message_id", sa.String(36), sa.ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True),
            sa.Column("pending", sa.Boolean(), nullable=False),
            sa.Column("processing", sa.Boolean(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    state_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("conversation_memory_states")}
    for column in ("user_id", "pending"):
        name = f"ix_conversation_memory_states_{column}"
        if name not in state_indexes:
            op.create_index(name, "conversation_memory_states", [column])


def downgrade() -> None:
    op.drop_table("conversation_memory_states")
    op.drop_table("conversation_memory_signals")
