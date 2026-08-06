"""Add immutable profile, analysis, memory, and conversation history.

Revision ID: 20260803_04
Revises: 20260803_03
"""

from datetime import datetime, timezone
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260803_04"
down_revision = "20260803_03"
branch_labels = None
depends_on = None


def _id() -> str:
    return str(uuid4())


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    connection = op.get_bind()
    tables = _tables()

    if "profile_versions" not in tables:
        op.create_table(
            "profile_versions",
            sa.Column("version_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("snapshot_data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id", "version_number", name="uq_profile_version_user_number"
            ),
        )
        op.create_index("ix_profile_versions_user_id", "profile_versions", ["user_id"])
        op.create_index(
            "ix_profile_versions_created_at", "profile_versions", ["created_at"]
        )

    if "current_version_id" not in _columns("profiles"):
        with op.batch_alter_table("profiles") as batch:
            batch.add_column(
                sa.Column("current_version_id", sa.String(36), nullable=True)
            )
            batch.create_foreign_key(
                "fk_profiles_current_version",
                "profile_versions",
                ["current_version_id"],
                ["version_id"],
            )
            batch.create_index(
                "ix_profiles_current_version_id", ["current_version_id"]
            )

    if "profile_document_sources" not in _tables():
        op.create_table(
            "profile_document_sources",
            sa.Column(
                "profile_version_id",
                sa.String(36),
                sa.ForeignKey("profile_versions.version_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.document_id", ondelete="RESTRICT"),
                primary_key=True,
            ),
        )

    profile_version_by_user: dict[str, str] = {}
    profile_rows = connection.execute(
        sa.text("SELECT * FROM profiles WHERE current_version_id IS NULL")
    ).mappings()
    for profile in profile_rows:
        user = connection.execute(
            sa.text("SELECT name, email FROM users WHERE user_id=:user_id"),
            {"user_id": profile["user_id"]},
        ).mappings().one()
        preferences = connection.execute(
            sa.text(
                "SELECT * FROM career_preferences WHERE user_id=:user_id"
            ),
            {"user_id": profile["user_id"]},
        ).mappings().first()
        skills = connection.execute(
            sa.text("SELECT skill_name FROM skills WHERE user_id=:user_id"),
            {"user_id": profile["user_id"]},
        ).scalars().all()
        projects = connection.execute(
            sa.text(
                "SELECT title, description FROM projects WHERE user_id=:user_id"
            ),
            {"user_id": profile["user_id"]},
        ).mappings().all()
        experience = connection.execute(
            sa.text(
                "SELECT organization, role, description FROM experience "
                "WHERE user_id=:user_id"
            ),
            {"user_id": profile["user_id"]},
        ).mappings().all()
        snapshot = {
            "name": user["name"],
            "email": user["email"],
            "education": _json_value(profile["education"], []),
            "school": profile["school"],
            "major": profile["major"],
            "graduation_year": profile["graduation_year"],
            "career_goal": profile["career_goal"],
            "skills": list(skills),
            "courses": [],
            "achievements": [],
            "certifications": [],
            "projects": [dict(item) for item in projects],
            "experience": [dict(item) for item in experience],
            "target_roles": (
                _json_value(preferences["target_roles"], [])
                if preferences
                else []
            ),
            "preferred_locations": (
                _json_value(preferences["preferred_locations"], [])
                if preferences
                else []
            ),
            "employment_types": (
                _json_value(preferences["employment_types"], [])
                if preferences
                else []
            ),
            "work_authorization": (
                preferences["work_authorization"] if preferences else None
            ),
            "remote_preference": (
                preferences["remote_preference"] if preferences else None
            ),
        }
        version_id = _id()
        profile_version_by_user[profile["user_id"]] = version_id
        connection.execute(
            sa.text(
                "INSERT INTO profile_versions "
                "(version_id, user_id, version_number, snapshot_data, created_at) "
                "VALUES (:version_id, :user_id, :version_number, "
                ":snapshot_data, :created_at)"
            ).bindparams(sa.bindparam("snapshot_data", type_=sa.JSON())),
            {
                "version_id": version_id,
                "user_id": profile["user_id"],
                "version_number": profile["version"],
                "snapshot_data": snapshot,
                "created_at": profile["updated_at"],
            },
        )
        connection.execute(
            sa.text(
                "UPDATE profiles SET current_version_id=:version_id "
                "WHERE profile_id=:profile_id"
            ),
            {"version_id": version_id, "profile_id": profile["profile_id"]},
        )

    for row in connection.execute(
        sa.text("SELECT user_id, current_version_id FROM profiles")
    ).mappings():
        profile_version_by_user[row["user_id"]] = row["current_version_id"]

    if "career_analysis_versions" not in _tables():
        op.create_table(
            "career_analysis_versions",
            sa.Column("analysis_version_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "profile_version_id",
                sa.String(36),
                sa.ForeignKey("profile_versions.version_id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("analysis_data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id", "version_number", name="uq_analysis_version_user_number"
            ),
        )
        op.create_index(
            "ix_career_analysis_versions_user_id",
            "career_analysis_versions",
            ["user_id"],
        )
        op.create_index(
            "ix_career_analysis_versions_profile_version_id",
            "career_analysis_versions",
            ["profile_version_id"],
        )
        op.create_index(
            "ix_career_analysis_versions_created_at",
            "career_analysis_versions",
            ["created_at"],
        )

    analysis_columns = _columns("career_analysis")
    if "current_version_id" not in analysis_columns:
        with op.batch_alter_table("career_analysis") as batch:
            batch.add_column(
                sa.Column("current_version_id", sa.String(36), nullable=True)
            )
            batch.add_column(
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
            )

        rows = connection.execute(
            sa.text(
                "SELECT * FROM career_analysis "
                "ORDER BY user_id, generated_at, analysis_id"
            )
        ).mappings().all()
        current_row_by_user: dict[str, tuple[str, str, datetime]] = {}
        count_by_user: dict[str, int] = {}
        for row in rows:
            profile_version_id = profile_version_by_user.get(row["user_id"])
            if profile_version_id is None:
                continue
            count_by_user[row["user_id"]] = count_by_user.get(row["user_id"], 0) + 1
            version_id = _id()
            connection.execute(
                sa.text(
                    "INSERT INTO career_analysis_versions "
                    "(analysis_version_id, user_id, profile_version_id, "
                    "version_number, analysis_data, created_at) VALUES "
                    "(:id, :user_id, :profile_id, :number, :data, :created_at)"
                ).bindparams(sa.bindparam("data", type_=sa.JSON())),
                {
                    "id": version_id,
                    "user_id": row["user_id"],
                    "profile_id": profile_version_id,
                    "number": count_by_user[row["user_id"]],
                    "data": {
                        "strengths": _json_value(row["strengths"], []),
                        "possible_roles": _json_value(row["possible_roles"], []),
                        "recommended_next_skills": (
                            _json_value(row["recommended_next_skills"], [])
                        ),
                    },
                    "created_at": row["generated_at"],
                },
            )
            current_row_by_user[row["user_id"]] = (
                row["analysis_id"],
                version_id,
                row["generated_at"],
            )

        keep_ids = []
        for analysis_id, version_id, generated_at in current_row_by_user.values():
            keep_ids.append(analysis_id)
            connection.execute(
                sa.text(
                    "UPDATE career_analysis SET current_version_id=:version_id, "
                    "created_at=:created_at, updated_at=:created_at "
                    "WHERE analysis_id=:analysis_id"
                ),
                {
                    "version_id": version_id,
                    "created_at": generated_at,
                    "analysis_id": analysis_id,
                },
            )
        if keep_ids:
            delete_old_analysis = sa.text(
                "DELETE FROM career_analysis WHERE analysis_id NOT IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True))
            connection.execute(
                delete_old_analysis,
                {"ids": keep_ids},
            )

        analysis_indexes = {
            item["name"]
            for item in sa.inspect(connection).get_indexes("career_analysis")
        }
        if "ix_career_analysis_generated_at" in analysis_indexes:
            op.drop_index(
                "ix_career_analysis_generated_at", table_name="career_analysis"
            )

        with op.batch_alter_table("career_analysis") as batch:
            for name in (
                "strengths",
                "possible_roles",
                "recommended_next_skills",
                "profile_version_used",
                "generated_at",
            ):
                if name in analysis_columns:
                    batch.drop_column(name)
            batch.alter_column(
                "created_at", existing_type=sa.DateTime(timezone=True), nullable=False
            )
            batch.alter_column(
                "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
            )
            batch.create_unique_constraint("uq_career_analysis_user_id", ["user_id"])
            batch.create_foreign_key(
                "fk_career_analysis_current_version",
                "career_analysis_versions",
                ["current_version_id"],
                ["analysis_version_id"],
            )
            batch.create_index(
                "ix_career_analysis_current_version_id", ["current_version_id"]
            )

    _create_memory_tables()
    _create_conversation_tables()


def _create_memory_tables() -> None:
    if "memory_candidates" not in _tables():
        op.create_table(
            "memory_candidates",
            sa.Column("candidate_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("category", sa.String(100), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("source", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_memory_candidates_user_id", "memory_candidates", ["user_id"])
        op.create_index(
            "ix_memory_candidates_created_at", "memory_candidates", ["created_at"]
        )
    if "memories" not in _tables():
        op.create_table(
            "memories",
            sa.Column("memory_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("category", sa.String(100), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("source", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_memories_user_id", "memories", ["user_id"])
        op.create_index("ix_memories_created_at", "memories", ["created_at"])


def _create_conversation_tables() -> None:
    if "conversations" not in _tables():
        op.create_table(
            "conversations",
            sa.Column("conversation_id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    if "messages" not in _tables():
        op.create_table(
            "messages",
            sa.Column("message_id", sa.String(36), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.String(36),
                sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
        op.create_index("ix_messages_created_at", "messages", ["created_at"])


def downgrade() -> None:
    # History-bearing migrations are intentionally not destructively downgraded.
    raise RuntimeError("Versioned memory cannot be safely downgraded.")
