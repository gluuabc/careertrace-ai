"""Add field-level profile history and retrieval sync status.

Revision ID: 20260815_12
Revises: 20260813_11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_12"
down_revision = "20260813_11"
branch_labels = None
depends_on = None


def _ensure_indexes(table: str, columns: tuple[str, ...]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for column in columns:
        name = f"ix_{table}_{column}"
        if name not in existing:
            op.create_index(name, table, [column])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    profile_version_columns = {
        item["name"] for item in inspector.get_columns("profile_versions")
    }
    with op.batch_alter_table("profile_versions") as batch:
        if "retrieval_index_status" not in profile_version_columns:
            batch.add_column(
                sa.Column(
                    "retrieval_index_status",
                    sa.String(30),
                    nullable=False,
                    server_default="pending",
                )
            )
        if "retrieval_index_error" not in profile_version_columns:
            batch.add_column(
                sa.Column("retrieval_index_error", sa.String(200), nullable=True)
            )

    if "profile_field_revisions" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "profile_field_revisions",
            sa.Column("revision_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("field_key", sa.String(100), nullable=False),
            sa.Column("operation", sa.String(30), nullable=False),
            sa.Column("previous_value", sa.JSON(), nullable=True),
            sa.Column("new_value", sa.JSON(), nullable=True),
            sa.Column("source_type", sa.String(30), nullable=False),
            sa.Column(
                "source_conversation_id",
                sa.String(36),
                sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("source_message_ids", sa.JSON(), nullable=False),
            sa.Column(
                "resulting_profile_version_id",
                sa.String(36),
                sa.ForeignKey("profile_versions.version_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
        )
    _ensure_indexes("profile_versions", ("retrieval_index_status",))
    _ensure_indexes(
        "profile_field_revisions",
        ("user_id", "field_key", "source_type", "source_conversation_id", "resulting_profile_version_id", "created_at"),
    )


def downgrade() -> None:
    op.drop_table("profile_field_revisions")
    with op.batch_alter_table("profile_versions") as batch:
        batch.drop_index("ix_profile_versions_retrieval_index_status")
        batch.drop_column("retrieval_index_error")
        batch.drop_column("retrieval_index_status")
