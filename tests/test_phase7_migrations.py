from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAD_REVISION = "20260817_17"
load_dotenv(PROJECT_ROOT / ".env")


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
    finally:
        engine.dispose()
    _upgrade(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            semantic = connection.execute(text("SELECT semantic_group, value FROM semantic_memories WHERE semantic_memory_id='m1'")).one()
            event = connection.execute(text("SELECT event_status, content FROM career_events WHERE career_event_id='m2'")).one()
        assert semantic == ("preference", '"remote roles"')
        assert event == ("unknown", "built a project")
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
