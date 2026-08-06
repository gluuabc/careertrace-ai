"""Add Google identity fields to users.

Revision ID: 20260731_02
Revises: 20260731_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260731_02"
down_revision = "20260731_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    with op.batch_alter_table("users") as batch:
        if "google_id" not in columns:
            batch.add_column(
                sa.Column("google_id", sa.String(length=255), nullable=True)
            )
            batch.create_unique_constraint("uq_users_google_id", ["google_id"])
        if "profile_image" not in columns:
            batch.add_column(
                sa.Column("profile_image", sa.Text(), nullable=True)
            )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    with op.batch_alter_table("users") as batch:
        if "profile_image" in columns:
            batch.drop_column("profile_image")
        if "google_id" in columns:
            batch.drop_constraint("uq_users_google_id", type_="unique")
            batch.drop_column("google_id")
