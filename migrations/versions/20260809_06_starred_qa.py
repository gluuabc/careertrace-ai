"""Add deterministic message pairing and persistent starred Q&A.

Revision ID: 20260809_06
Revises: 20260806_05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260809_06"
down_revision = "20260806_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    sqlite = bind.dialect.name == "sqlite"
    message_columns = {item["name"] for item in inspector.get_columns("messages")}
    message_indexes = {item["name"] for item in inspector.get_indexes("messages")}
    message_foreign_columns = {
        tuple(item.get("constrained_columns") or ())
        for item in inspector.get_foreign_keys("messages")
    }
    if sqlite:
        with op.batch_alter_table("messages") as batch:
            if "reply_to_message_id" not in message_columns:
                batch.add_column(sa.Column("reply_to_message_id", sa.String(36), nullable=True))
            if ("reply_to_message_id",) not in message_foreign_columns:
                batch.create_foreign_key(
                    "fk_messages_reply_to_message_id",
                    "messages",
                    ["reply_to_message_id"],
                    ["message_id"],
                    ondelete="SET NULL",
                )
            if "ix_messages_reply_to_message_id" not in message_indexes:
                batch.create_index("ix_messages_reply_to_message_id", ["reply_to_message_id"])
    else:
        if "reply_to_message_id" not in message_columns:
            op.add_column(
                "messages",
                sa.Column(
                    "reply_to_message_id",
                    sa.String(36),
                    sa.ForeignKey("messages.message_id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
        if "ix_messages_reply_to_message_id" not in message_indexes:
            op.create_index("ix_messages_reply_to_message_id", "messages", ["reply_to_message_id"])

    inspector = sa.inspect(bind)
    run_columns = {item["name"] for item in inspector.get_columns("agent_runs")}
    run_indexes = {item["name"] for item in inspector.get_indexes("agent_runs")}
    run_foreign_columns = {
        tuple(item.get("constrained_columns") or ())
        for item in inspector.get_foreign_keys("agent_runs")
    }
    if sqlite:
        with op.batch_alter_table("agent_runs") as batch:
            for name in ("user_message_id", "assistant_message_id"):
                if name not in run_columns:
                    batch.add_column(sa.Column(name, sa.String(36), nullable=True))
                if (name,) not in run_foreign_columns:
                    batch.create_foreign_key(
                        f"fk_agent_runs_{name}",
                        "messages",
                        [name],
                        ["message_id"],
                        ondelete="SET NULL",
                    )
                if f"ix_agent_runs_{name}" not in run_indexes:
                    batch.create_index(f"ix_agent_runs_{name}", [name])
            if "state_json" not in run_columns:
                batch.add_column(
                    sa.Column("state_json", sa.JSON(), nullable=False, server_default="{}")
                )
    else:
        for name in ("user_message_id", "assistant_message_id"):
            if name not in run_columns:
                op.add_column(
                    "agent_runs",
                    sa.Column(
                        name,
                        sa.String(36),
                        sa.ForeignKey("messages.message_id", ondelete="SET NULL"),
                        nullable=True,
                    ),
                )
            if f"ix_agent_runs_{name}" not in run_indexes:
                op.create_index(f"ix_agent_runs_{name}", "agent_runs", [name])
        if "state_json" not in run_columns:
            op.add_column(
                "agent_runs",
                sa.Column("state_json", sa.JSON(), nullable=False, server_default="{}"),
            )

    if "starred_qa_pairs" not in set(inspector.get_table_names()):
        op.create_table(
            "starred_qa_pairs",
            sa.Column("starred_qa_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "conversation_id",
                sa.String(36),
                sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_message_id",
                sa.String(36),
                sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "assistant_message_id",
                sa.String(36),
                sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("preference_update_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "user_message_id",
                "assistant_message_id",
                name="uq_starred_qa_user_pair",
            ),
        )
        for column in ("user_id", "conversation_id", "created_at"):
            op.create_index(
                f"ix_starred_qa_pairs_{column}", "starred_qa_pairs", [column]
            )


def downgrade() -> None:
    op.drop_table("starred_qa_pairs")
    op.drop_column("agent_runs", "state_json")
    for name in ("assistant_message_id", "user_message_id"):
        op.drop_index(f"ix_agent_runs_{name}", table_name="agent_runs")
        op.drop_column("agent_runs", name)
    op.drop_index("ix_messages_reply_to_message_id", table_name="messages")
    op.drop_column("messages", "reply_to_message_id")
