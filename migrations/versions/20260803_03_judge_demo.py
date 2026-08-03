"""Mark fixed synthetic judge-demo users.

Revision ID: 20260803_03
Revises: 20260731_02
"""

from alembic import op
import sqlalchemy as sa

revision = "20260803_03"
down_revision = "20260731_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "is_demo" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(
                sa.Column(
                    "is_demo",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "is_demo" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("is_demo")
