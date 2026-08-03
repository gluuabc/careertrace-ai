"""Add documents and enforce complete profile facts.

Revision ID: 20260731_01
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "20260731_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())

    if "users" not in tables:
        from app.database.database import Base
        from app.database import models  # noqa: F401

        Base.metadata.create_all(bind=connection)
        return

    invalid_profiles = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM profiles
            WHERE school IS NULL OR TRIM(school) = ''
               OR major IS NULL OR TRIM(major) = ''
               OR graduation_year IS NULL
            """
        )
    ).scalar_one()
    if invalid_profiles:
        raise RuntimeError(
            "Cannot enforce required profile fields while incomplete rows exist."
        )

    profile_columns = {
        column["name"]: column for column in inspector.get_columns("profiles")
    }
    if any(
        profile_columns[name]["nullable"]
        for name in ("school", "major", "graduation_year")
    ):
        with op.batch_alter_table("profiles") as batch:
            batch.alter_column(
                "school",
                existing_type=sa.String(length=300),
                nullable=False,
            )
            batch.alter_column(
                "major",
                existing_type=sa.String(length=300),
                nullable=False,
            )
            batch.alter_column(
                "graduation_year",
                existing_type=sa.Integer(),
                nullable=False,
            )

    analysis_columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns("career_analysis")
    }
    if (
        "profile_version" in analysis_columns
        and "profile_version_used" not in analysis_columns
    ):
        with op.batch_alter_table("career_analysis") as batch:
            batch.alter_column(
                "profile_version",
                new_column_name="profile_version_used",
                existing_type=sa.Integer(),
                nullable=False,
            )

    if "documents" not in sa.inspect(connection).get_table_names():
        op.create_table(
            "documents",
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("filename", sa.String(length=500), nullable=False),
            sa.Column("s3_key", sa.String(length=1024), nullable=False),
            sa.Column("document_type", sa.String(length=50), nullable=False),
            sa.Column("content_type", sa.String(length=255), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.user_id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("document_id"),
            sa.UniqueConstraint("s3_key"),
        )
        op.create_index("ix_documents_user_id", "documents", ["user_id"])
        op.create_index("ix_documents_uploaded_at", "documents", ["uploaded_at"])


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "documents" in inspector.get_table_names():
        op.drop_index("ix_documents_uploaded_at", table_name="documents")
        op.drop_index("ix_documents_user_id", table_name="documents")
        op.drop_table("documents")

    analysis_columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns("career_analysis")
    }
    if "profile_version_used" in analysis_columns:
        with op.batch_alter_table("career_analysis") as batch:
            batch.alter_column(
                "profile_version_used",
                new_column_name="profile_version",
                existing_type=sa.Integer(),
                nullable=False,
            )

    with op.batch_alter_table("profiles") as batch:
        batch.alter_column(
            "school", existing_type=sa.String(length=300), nullable=True
        )
        batch.alter_column(
            "major", existing_type=sa.String(length=300), nullable=True
        )
        batch.alter_column(
            "graduation_year", existing_type=sa.Integer(), nullable=True
        )
