from __future__ import annotations

import os
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAD_REVISION = "20260817_17"
load_dotenv(PROJECT_ROOT / ".env")


def _memory_migration_module():
    path = PROJECT_ROOT / "migrations" / "versions" / "20260817_17_semantic_and_episodic_memory.py"
    spec = importlib.util.spec_from_file_location("memory_migration_17", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_memory_upgrade_directly(connection) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        _memory_migration_module().upgrade()


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.upgrade(config, revision)


def _assert_head(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            tables = set(inspect(connection).get_table_names())
        assert revision == HEAD_REVISION
        assert {"users", "profile_versions", "memories", "semantic_memories", "career_events", "career_paths", "judge_workspace_credentials"} <= tables
    finally:
        engine.dispose()


def test_fresh_sqlite_migrations_reach_head(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fresh.sqlite'}"
    _upgrade(database_url)
    _assert_head(database_url)


def test_existing_sqlite_upgrade_reaches_head(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'existing.sqlite'}"
    _upgrade(database_url, "20260803_04")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260803_04"
    finally:
        engine.dispose()
    _upgrade(database_url)
    _assert_head(database_url)


def test_legacy_memories_are_preserved_in_typed_tables(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'memory-backfill.sqlite'}"
    _upgrade(database_url, "20260815_16")
    engine = create_engine(database_url)
    now = "2026-08-17 00:00:00+00:00"
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (user_id, name, is_demo, created_at, updated_at) VALUES ('u1', 'Test', 0, :now, :now)"), {"now": now})
            base = """INSERT INTO memories
                (memory_id, user_id, category, content, confidence, source, active,
                 source_message_ids, retrieval_index_status, created_at)
                VALUES (:id, 'u1', :category, :content, NULL, 'test', 1, '[]', 'pending', :now)"""
            connection.execute(text(base), {"id": "m1", "category": "preference", "content": "remote roles", "now": now})
            connection.execute(text(base), {"id": "m2", "category": "event", "content": "built a project", "now": now})
            connection.execute(text(base), {"id": "m3", "category": "goal", "content": "ML engineer", "now": now})
            connection.execute(text("UPDATE memories SET supersedes_memory_id='m1' WHERE memory_id='m3'"))
            connection.execute(text(base), {"id": "m4", "category": "event", "content": "started another project", "now": now})
            connection.execute(text("UPDATE memories SET supersedes_memory_id='m2' WHERE memory_id='m4'"))
            connection.execute(text(base), {"id": "m5", "category": "event", "content": "cross-type legacy history", "now": now})
            connection.execute(text("UPDATE memories SET supersedes_memory_id='m1' WHERE memory_id='m5'"))
            candidate = """INSERT INTO memory_candidates
                (candidate_id, user_id, category, content, confidence, source, operation,
                 source_message_ids, memory_kind, proposal_sources, status, created_at)
                VALUES (:id, 'u1', :category, :content, NULL, 'test', 'ADD', '[]',
                        'semantic', '[]', 'pending', :now)"""
            connection.execute(text(candidate), {"id": "c1", "category": "event", "content": "planned event", "now": now})
            connection.execute(text(candidate), {"id": "c2", "category": "goal", "content": "career goal", "now": now})
    finally:
        engine.dispose()
    _upgrade(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            semantic = connection.execute(text("SELECT semantic_group, value FROM semantic_memories WHERE semantic_memory_id='m1'")).one()
            event = connection.execute(text("SELECT event_status, content FROM career_events WHERE career_event_id='m2'")).one()
            semantic_link = connection.scalar(text("SELECT supersedes_semantic_memory_id FROM semantic_memories WHERE semantic_memory_id='m3'"))
            event_link = connection.scalar(text("SELECT supersedes_event_id FROM career_events WHERE career_event_id='m4'"))
            cross_type = connection.scalar(text("SELECT supersedes_event_id FROM career_events WHERE career_event_id='m5'"))
            candidates = connection.execute(text("SELECT candidate_id, memory_kind, semantic_group, event_status FROM memory_candidates ORDER BY candidate_id")).all()
        assert semantic == ("preference", '"remote roles"')
        assert event == ("unknown", "built a project")
        assert semantic_link == "m1"
        assert event_link == "m2"
        assert cross_type is None
        assert candidates == [
            ("c1", "episodic", None, "unknown"),
            ("c2", "semantic", "goal", None),
        ]
    finally:
        engine.dispose()


def test_memory_migration_is_safe_to_run_again_after_data_copy(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'retry.sqlite'}"
    _upgrade(database_url, "20260815_16")
    engine = create_engine(database_url)
    now = "2026-08-17 00:00:00+00:00"
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (user_id, name, is_demo, created_at, updated_at) VALUES ('u1', 'Test', 0, :now, :now)"), {"now": now})
            connection.execute(text("""INSERT INTO memories
                (memory_id, user_id, category, content, confidence, source, active,
                 source_message_ids, retrieval_index_status, created_at)
                VALUES ('m1', 'u1', 'preference', 'remote', NULL, 'test', 1, '[]', 'pending', :now)"""), {"now": now})
            # Simulate an interrupted attempt that copied one row but did not
            # advance Alembic's revision marker.
            connection.execute(text("""INSERT INTO semantic_memories
                (semantic_memory_id, user_id, semantic_group, value, source,
                 source_message_ids, active, retrieval_index_status, created_at)
                VALUES ('m1', 'u1', 'preference', '"remote"', 'test', '[]', 1, 'pending', :now)"""), {"now": now})
        with engine.begin() as connection:
            _run_memory_upgrade_directly(connection)
        with engine.begin() as connection:
            _run_memory_upgrade_directly(connection)
            assert connection.scalar(text("SELECT count(*) FROM semantic_memories")) == 1
            assert connection.scalar(text("SELECT count(*) FROM career_events")) == 0
    finally:
        engine.dispose()


def test_memory_migration_validation_rejects_candidate_misclassification(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'validation.sqlite'}"
    _upgrade(database_url, "20260815_16")
    engine = create_engine(database_url)
    now = "2026-08-17 00:00:00+00:00"
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (user_id, name, is_demo, created_at, updated_at) VALUES ('u1', 'Test', 0, :now, :now)"), {"now": now})
            connection.execute(text("""INSERT INTO memory_candidates
                (candidate_id, user_id, category, content, confidence, source, operation,
                 source_message_ids, memory_kind, proposal_sources, status, created_at)
                VALUES ('c1', 'u1', 'event', 'event', NULL, 'test', 'ADD', '[]',
                        'semantic', '[]', 'pending', :now)"""), {"now": now})
        with engine.begin() as connection:
            _run_memory_upgrade_directly(connection)
            connection.execute(text("UPDATE memory_candidates SET memory_kind='semantic' WHERE candidate_id='c1'"))
            with pytest.raises(RuntimeError, match="event candidate"):
                _memory_migration_module()._validate_migration(connection)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("COCKROACH_TEST_DATABASE_URL"),
    reason="isolated Cockroach test URL not configured",
)
def test_fresh_cockroach_migrations_reach_head():
    base_url = os.environ["COCKROACH_TEST_DATABASE_URL"]
    engine = create_engine(base_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            database = str(connection.scalar(text("SELECT current_database()")))
            if "test" not in database.casefold():
                raise RuntimeError("Refusing to reset a Cockroach database not named as a test database.")
            table_names = inspect(connection).get_table_names(schema="public")
            if table_names:
                quoted = ", ".join(
                    '"' + name.replace('"', '""') + '"' for name in table_names
                )
                connection.execute(text(f"DROP TABLE {quoted} CASCADE"))
        _upgrade(base_url)
        _assert_head(base_url)
    finally:
        engine.dispose()
