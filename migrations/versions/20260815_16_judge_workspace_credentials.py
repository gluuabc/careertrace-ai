"""Add hashed per-workspace Judge recovery credentials.

Revision ID: 20260815_16
Revises: 20260815_15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_16"
down_revision = "20260815_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "judge_workspace_credentials" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "judge_workspace_credentials",
            sa.Column("credential_id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("recovery_code_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
    existing = {item["name"] for item in sa.inspect(bind).get_indexes("judge_workspace_credentials")}
    for column in ("user_id", "recovery_code_hash", "created_at", "revoked_at"):
        name = f"ix_judge_workspace_credentials_{column}"
        if name not in existing:
            op.create_index(name, "judge_workspace_credentials", [column], unique=column == "recovery_code_hash")


def downgrade() -> None:
    op.drop_table("judge_workspace_credentials")
