"""Add durable semantic memories, career events, and career paths.

Revision ID: 20260817_17
Revises: 20260815_16
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime
import json


revision = "20260817_17"
down_revision = "20260815_16"
branch_labels = None
depends_on = None

BACKFILL_BATCH_SIZE = 500


def _timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _json_list(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def _scalar(connection, statement: str, **parameters):
    return connection.scalar(sa.text(statement), parameters)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Memory migration validation failed: {message}")


def _add_candidate_columns() -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("memory_candidates")}
    columns = [
        sa.Column("memory_kind", sa.String(30), nullable=False, server_default="semantic"),
        sa.Column("existing_entity_id", sa.String(36), nullable=True),
        sa.Column("semantic_group", sa.String(100), nullable=True),
        sa.Column("topic_key", sa.String(200), nullable=True),
        sa.Column("proposed_value", sa.JSON(), nullable=True),
        sa.Column("event_status", sa.String(30), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("evidence_start", sa.Integer(), nullable=True),
        sa.Column("evidence_end", sa.Integer(), nullable=True),
        sa.Column("proposal_sources", sa.JSON(), nullable=False, server_default="[]"),
    ]
    for column in columns:
        if column.name not in existing:
            op.add_column("memory_candidates", column)


def _classify_legacy_candidates(connection) -> None:
    """Deterministically route every pre-redesign candidate by legacy category."""

    connection.execute(sa.text("""
        UPDATE memory_candidates
        SET memory_kind = CASE WHEN category = 'event' THEN 'episodic' ELSE 'semantic' END,
            semantic_group = CASE WHEN category = 'event' THEN NULL ELSE category END,
            event_status = CASE
                WHEN category = 'event' THEN coalesce(event_status, 'unknown')
                ELSE NULL
            END
    """))


def upgrade() -> None:
    _add_candidate_columns()
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "career_paths" not in tables:
        op.create_table(
        "career_paths",
        sa.Column("career_path_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "semantic_memories" not in tables:
        op.create_table(
        "semantic_memories",
        sa.Column("semantic_memory_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("semantic_group", sa.String(100), nullable=False),
        sa.Column("topic_key", sa.String(200), nullable=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("supersedes_semantic_memory_id", sa.String(36), sa.ForeignKey("semantic_memories.semantic_memory_id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_index_status", sa.String(30), nullable=False),
        sa.Column("retrieval_index_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "career_events" not in tables:
        op.create_table(
        "career_events",
        sa.Column("career_event_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("career_path_id", sa.String(36), sa.ForeignKey("career_paths.career_path_id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("event_status", sa.String(30), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_temporal_expression", sa.String(200), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("supersedes_event_id", sa.String(36), sa.ForeignKey("career_events.career_event_id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_index_status", sa.String(30), nullable=False),
        sa.Column("retrieval_index_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    for table, columns in {
        "career_paths": ("user_id", "active"),
        "semantic_memories": ("user_id", "semantic_group", "topic_key", "active", "created_at"),
        "career_events": ("user_id", "career_path_id", "event_status", "active", "created_at"),
        "memory_candidates": ("memory_kind", "existing_entity_id", "semantic_group", "topic_key", "event_status"),
    }.items():
        existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
        for column in columns:
            name = f"ix_{table}_{column}"
            if name not in existing:
                op.create_index(name, table, [column])

    connection = op.get_bind()
    _classify_legacy_candidates(connection)
    semantic = sa.table(
        "semantic_memories",
        sa.column("semantic_memory_id", sa.String), sa.column("user_id", sa.String),
        sa.column("semantic_group", sa.String), sa.column("topic_key", sa.String),
        sa.column("value", sa.JSON), sa.column("source", sa.String),
        sa.column("source_conversation_id", sa.String), sa.column("source_message_ids", sa.JSON),
        sa.column("evidence_text", sa.Text), sa.column("active", sa.Boolean),
        sa.column("supersedes_semantic_memory_id", sa.String), sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("retrieval_index_status", sa.String), sa.column("retrieval_index_error", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    events = sa.table(
        "career_events",
        sa.column("career_event_id", sa.String), sa.column("user_id", sa.String),
        sa.column("career_path_id", sa.String), sa.column("content", sa.Text),
        sa.column("event_status", sa.String), sa.column("event_time", sa.DateTime(timezone=True)),
        sa.column("raw_temporal_expression", sa.String), sa.column("title", sa.String),
        sa.column("description", sa.Text), sa.column("start_date", sa.DateTime(timezone=True)),
        sa.column("end_date", sa.DateTime(timezone=True)), sa.column("outcome", sa.Text),
        sa.column("source", sa.String), sa.column("source_conversation_id", sa.String),
        sa.column("source_message_ids", sa.JSON), sa.column("evidence_text", sa.Text),
        sa.column("active", sa.Boolean), sa.column("supersedes_event_id", sa.String),
        sa.column("revoked_at", sa.DateTime(timezone=True)), sa.column("retrieval_index_status", sa.String),
        sa.column("retrieval_index_error", sa.Text), sa.column("created_at", sa.DateTime(timezone=True)),
    )

    # Pass 1: copy every legacy row without self-references. Primary-key
    # existence checks make this safe to retry after a partially completed run.
    last_memory_id = ""
    while True:
        rows = connection.execute(sa.text("""
            SELECT * FROM memories
            WHERE memory_id > :last_memory_id
            ORDER BY memory_id
            LIMIT :batch_size
        """), {
            "last_memory_id": last_memory_id,
            "batch_size": BACKFILL_BATCH_SIZE,
        }).mappings().all()
        if not rows:
            break
        for row in rows:
            if row["category"] == "event":
                exists = connection.scalar(sa.select(events.c.career_event_id).where(
                    events.c.career_event_id == row["memory_id"]
                ))
                if exists is None:
                    connection.execute(events.insert().values(
                        career_event_id=row["memory_id"], user_id=row["user_id"], career_path_id=None,
                        content=row["content"], event_status="unknown", event_time=_timestamp(row.get("event_time")),
                        raw_temporal_expression=None, title=None, description=None, start_date=None, end_date=None,
                        outcome=None, source=row["source"], source_conversation_id=row.get("source_conversation_id"),
                        source_message_ids=_json_list(row.get("source_message_ids")), evidence_text=None,
                        active=row.get("active", True), supersedes_event_id=None,
                        revoked_at=_timestamp(row.get("revoked_at")), retrieval_index_status=row.get("retrieval_index_status") or "pending",
                        retrieval_index_error=row.get("retrieval_index_error"), created_at=_timestamp(row["created_at"]),
                    ))
            else:
                exists = connection.scalar(sa.select(semantic.c.semantic_memory_id).where(
                    semantic.c.semantic_memory_id == row["memory_id"]
                ))
                if exists is None:
                    connection.execute(semantic.insert().values(
                        semantic_memory_id=row["memory_id"], user_id=row["user_id"], semantic_group=row["category"],
                        topic_key=None, value=row["content"], source=row["source"],
                        source_conversation_id=row.get("source_conversation_id"), source_message_ids=_json_list(row.get("source_message_ids")),
                        evidence_text=None, active=row.get("active", True),
                        supersedes_semantic_memory_id=None, revoked_at=_timestamp(row.get("revoked_at")),
                        retrieval_index_status=row.get("retrieval_index_status") or "pending",
                        retrieval_index_error=row.get("retrieval_index_error"), created_at=_timestamp(row["created_at"]),
                    ))
            connection.execute(sa.text(
                "UPDATE retrieval_documents SET corpus_type = :corpus "
                "WHERE corpus_type = 'approved_memory' AND source_entity_id LIKE :prefix"
            ), {
                "corpus": "episodic_event" if row["category"] == "event" else "semantic_memory",
                "prefix": f"{row['memory_id']}%",
            })
        last_memory_id = rows[-1]["memory_id"]

    # Pass 2: all referenced target rows now exist. Copy only same-destination
    # relationships; cross-type history remains preserved in the legacy table.
    connection.execute(sa.text("""
        UPDATE semantic_memories
        SET supersedes_semantic_memory_id = (
            SELECT older.memory_id
            FROM memories AS newer
            JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
            WHERE newer.memory_id = semantic_memories.semantic_memory_id
              AND newer.category <> 'event'
              AND older.category <> 'event'
        )
        WHERE semantic_memory_id IN (
            SELECT newer.memory_id
            FROM memories AS newer
            JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
            WHERE newer.category <> 'event' AND older.category <> 'event'
        )
    """))
    connection.execute(sa.text("""
        UPDATE career_events
        SET supersedes_event_id = (
            SELECT older.memory_id
            FROM memories AS newer
            JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
            WHERE newer.memory_id = career_events.career_event_id
              AND newer.category = 'event'
              AND older.category = 'event'
        )
        WHERE career_event_id IN (
            SELECT newer.memory_id
            FROM memories AS newer
            JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
            WHERE newer.category = 'event' AND older.category = 'event'
        )
    """))

    _validate_migration(connection)


def _validate_migration(connection) -> None:
    expected_semantic = _scalar(connection, "SELECT count(*) FROM memories WHERE category <> 'event'")
    expected_events = _scalar(connection, "SELECT count(*) FROM memories WHERE category = 'event'")
    migrated_semantic = _scalar(connection, """
        SELECT count(*) FROM semantic_memories AS target
        JOIN memories AS legacy ON legacy.memory_id = target.semantic_memory_id
        WHERE legacy.category <> 'event'
    """)
    migrated_events = _scalar(connection, """
        SELECT count(*) FROM career_events AS target
        JOIN memories AS legacy ON legacy.memory_id = target.career_event_id
        WHERE legacy.category = 'event'
    """)
    _require(migrated_semantic == expected_semantic, "semantic memory count does not match non-event legacy memory count")
    _require(migrated_events == expected_events, "career event count does not match event legacy memory count")
    _require(_scalar(connection, """
        SELECT count(*) FROM semantic_memories AS target
        JOIN memories AS legacy ON legacy.memory_id = target.semantic_memory_id
        WHERE legacy.category = 'event'
           OR target.semantic_group IS DISTINCT FROM legacy.category
    """) == 0, "a semantic memory has the wrong legacy category mapping")
    _require(_scalar(connection, """
        SELECT count(*) FROM career_events AS target
        JOIN memories AS legacy ON legacy.memory_id = target.career_event_id
        WHERE legacy.category <> 'event'
           OR target.content IS DISTINCT FROM legacy.content
    """) == 0, "a career event has the wrong legacy mapping")
    _require(_scalar(connection, """
        SELECT count(*) FROM memory_candidates
        WHERE category = 'event'
          AND (
              memory_kind <> 'episodic'
              OR semantic_group IS NOT NULL
              OR event_status IS NULL
          )
    """) == 0, "an event candidate was not classified as episodic")
    _require(_scalar(connection, """
        SELECT count(*) FROM memory_candidates
        WHERE category <> 'event'
          AND (
              memory_kind <> 'semantic'
              OR semantic_group IS DISTINCT FROM category
              OR event_status IS NOT NULL
          )
    """) == 0, "a non-event candidate was not classified as semantic")
    expected_semantic_links = _scalar(connection, """
        SELECT count(*) FROM memories AS newer
        JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
        WHERE newer.category <> 'event' AND older.category <> 'event'
    """)
    expected_event_links = _scalar(connection, """
        SELECT count(*) FROM memories AS newer
        JOIN memories AS older ON older.memory_id = newer.supersedes_memory_id
        WHERE newer.category = 'event' AND older.category = 'event'
    """)
    _require(_scalar(connection, """
        SELECT count(*) FROM semantic_memories WHERE supersedes_semantic_memory_id IS NOT NULL
    """) == expected_semantic_links, "same-type semantic supersession history was not preserved")
    _require(_scalar(connection, """
        SELECT count(*) FROM career_events WHERE supersedes_event_id IS NOT NULL
    """) == expected_event_links, "same-type event supersession history was not preserved")
    _require(_scalar(connection, """
        SELECT count(*) FROM semantic_memories AS newer
        LEFT JOIN semantic_memories AS older
          ON older.semantic_memory_id = newer.supersedes_semantic_memory_id
        WHERE newer.supersedes_semantic_memory_id IS NOT NULL
          AND older.semantic_memory_id IS NULL
    """) == 0, "semantic supersession contains an invalid reference")
    _require(_scalar(connection, """
        SELECT count(*) FROM career_events AS newer
        LEFT JOIN career_events AS older ON older.career_event_id = newer.supersedes_event_id
        WHERE newer.supersedes_event_id IS NOT NULL AND older.career_event_id IS NULL
    """) == 0, "event supersession contains an invalid reference")


def downgrade() -> None:
    raise RuntimeError("Downgrade is intentionally blocked because it would discard durable user memory history.")
