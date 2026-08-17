#!/usr/bin/env python3
"""Run the 20260815_16 -> 20260817_17 migration against disposable CockroachDB.

DATABASE_URL supplies cluster credentials and TLS options.  The script never
uses its database component: administration happens through ``defaultdb`` and
all migrations/data changes happen in a newly-created validation database.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "20260815_16"
TARGET_REVISION = "20260817_17"
DATABASE_PREFIX = "careertrace_migration_validation_"
SAFE_DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ValidationFailure(RuntimeError):
    pass


def _safe_name(requested: str | None) -> str:
    name = requested or (
        DATABASE_PREFIX
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_")
        + uuid.uuid4().hex[:8]
    )
    if not SAFE_DATABASE_NAME.fullmatch(name) or not name.startswith(DATABASE_PREFIX):
        raise ValidationFailure(
            f"Database name must start with {DATABASE_PREFIX!r} and contain only "
            "lowercase letters, digits, and underscores (maximum 63 characters)."
        )
    if name == "careertrace":
        raise ValidationFailure("Refusing to use the production database name.")
    return name


def _url_for_database(base: URL, database: str) -> URL:
    return base.set(database=database)


@contextmanager
def _database_url(url: URL):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _alembic_upgrade(url: URL, revision: str) -> float:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    rendered = url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    started = time.monotonic()
    with _database_url(url):
        command.upgrade(config, revision)
    return time.monotonic() - started


def _scalar(connection, sql: str, **params):
    return connection.scalar(text(sql), params)


def _check(name: str, actual, expected) -> tuple[str, bool, object, object]:
    return name, actual == expected, actual, expected


def _create_database(admin_url: URL, database: str) -> None:
    engine = create_engine(admin_url, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            if connection.dialect.name != "cockroachdb":
                raise ValidationFailure(
                    f"DATABASE_URL must use CockroachDB; detected {connection.dialect.name!r}."
                )
            exists = connection.scalar(
                text("SELECT 1 FROM [SHOW DATABASES] WHERE database_name = :database"),
                {"database": database},
            )
            if exists:
                raise ValidationFailure(
                    f"Disposable database {database!r} already exists; choose a new name."
                )
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    finally:
        engine.dispose()


def _insert_legacy_fixture(url: URL) -> None:
    now = datetime.now(timezone.utc)
    user_id = "00000000-0000-4000-8000-000000000001"
    memories = [
        ("10000000-0000-4000-8000-000000000001", "preference", "Remote-friendly work", None),
        ("10000000-0000-4000-8000-000000000002", "goal", "Become an ML engineer", None),
        ("10000000-0000-4000-8000-000000000003", "constraint", "Must remain in the US", None),
        ("10000000-0000-4000-8000-000000000004", "goal", "Lead an ML team", "10000000-0000-4000-8000-000000000002"),
        ("20000000-0000-4000-8000-000000000001", "event", "Completed a capstone", None),
        ("20000000-0000-4000-8000-000000000002", "event", "Started an internship", "20000000-0000-4000-8000-000000000001"),
        # Cross-type history must remain only in the legacy table.
        ("20000000-0000-4000-8000-000000000003", "event", "Changed career direction", "10000000-0000-4000-8000-000000000001"),
    ]
    candidates = [
        ("30000000-0000-4000-8000-000000000001", "event", "Planning a hackathon"),
        ("30000000-0000-4000-8000-000000000002", "preference", "Prefers small teams"),
    ]
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO users (user_id, name, is_demo, created_at, updated_at)
                        VALUES (:user_id, 'Migration Validation User', false, :now, :now)"""),
                {"user_id": user_id, "now": now},
            )
            for memory_id, category, content, supersedes in memories:
                connection.execute(
                    text("""INSERT INTO memories
                        (memory_id, user_id, category, content, confidence, source, active,
                         supersedes_memory_id, source_message_ids, retrieval_index_status, created_at)
                        VALUES (:id, :user_id, :category, :content, 0.95, 'migration_validation',
                                true, :supersedes, '[]', 'indexed', :now)"""),
                    {"id": memory_id, "user_id": user_id, "category": category,
                     "content": content, "supersedes": supersedes, "now": now},
                )
                connection.execute(
                    text("""INSERT INTO retrieval_documents
                        (retrieval_document_id, corpus_type, user_id, source_entity_id,
                         source_version, title, text, metadata_json, evidence_ids,
                         content_hash, retrieved_at, created_at, updated_at, active)
                        VALUES (:document_id, 'approved_memory', :user_id, :source_id,
                                '1', :title, :content, '{}', '[]', :content_hash,
                                :now, :now, :now, true)"""),
                    {"document_id": str(uuid.uuid4()), "user_id": user_id,
                     "source_id": memory_id, "title": category, "content": content,
                     "content_hash": uuid.uuid5(uuid.NAMESPACE_URL, memory_id).hex, "now": now},
                )
            for candidate_id, category, content in candidates:
                connection.execute(
                    text("""INSERT INTO memory_candidates
                        (candidate_id, user_id, category, content, confidence, source,
                         operation, source_message_ids, status, created_at)
                        VALUES (:id, :user_id, :category, :content, 0.9,
                                'migration_validation', 'ADD', '[]', 'pending', :now)"""),
                    {"id": candidate_id, "user_id": user_id, "category": category,
                     "content": content, "now": now},
                )
    finally:
        engine.dispose()


def _schema_jobs(connection) -> list[dict]:
    rows = connection.execute(text("SHOW JOBS")).mappings().all()
    selected = []
    target_objects = ("semantic_memories", "career_events", "career_paths", "memory_candidates")
    for row in rows:
        job_type = str(row.get("job_type") or row.get("type") or "").casefold()
        description = str(row.get("description") or "").casefold()
        if "schema" in job_type and any(name in description for name in target_objects):
            selected.append(dict(row))
    return selected


def _validate(url: URL) -> tuple[list[tuple], list[dict]]:
    expected_semantic_ids = {
        "10000000-0000-4000-8000-000000000001",
        "10000000-0000-4000-8000-000000000002",
        "10000000-0000-4000-8000-000000000003",
        "10000000-0000-4000-8000-000000000004",
    }
    expected_event_ids = {
        "20000000-0000-4000-8000-000000000001",
        "20000000-0000-4000-8000-000000000002",
        "20000000-0000-4000-8000-000000000003",
    }
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            semantic_ids = set(connection.scalars(text("SELECT semantic_memory_id FROM semantic_memories")))
            event_ids = set(connection.scalars(text("SELECT career_event_id FROM career_events")))
            groups = dict(connection.execute(text(
                "SELECT semantic_memory_id, semantic_group FROM semantic_memories"
            )).all())
            candidates = dict(connection.execute(text(
                "SELECT category, memory_kind FROM memory_candidates"
            )).all())
            corpus = dict(connection.execute(text(
                "SELECT source_entity_id, corpus_type FROM retrieval_documents"
            )).all())
            checks = [
                _check("Alembic revision", _scalar(connection, "SELECT version_num FROM alembic_version"), TARGET_REVISION),
                _check("Semantic legacy count", len(semantic_ids), 4),
                _check("Episodic legacy count", len(event_ids), 3),
                _check("All semantic IDs migrated", semantic_ids, expected_semantic_ids),
                _check("All event IDs migrated", event_ids, expected_event_ids),
                _check("Semantic groups", groups, {
                    "10000000-0000-4000-8000-000000000001": "preference",
                    "10000000-0000-4000-8000-000000000002": "goal",
                    "10000000-0000-4000-8000-000000000003": "constraint",
                    "10000000-0000-4000-8000-000000000004": "goal",
                }),
                _check("Candidate classification", candidates, {"event": "episodic", "preference": "semantic"}),
                _check("Event statuses", set(connection.scalars(text("SELECT event_status FROM career_events"))), {"unknown"}),
                _check("Semantic supersession", _scalar(connection, """SELECT supersedes_semantic_memory_id FROM semantic_memories
                    WHERE semantic_memory_id='10000000-0000-4000-8000-000000000004'"""), "10000000-0000-4000-8000-000000000002"),
                _check("Event supersession", _scalar(connection, """SELECT supersedes_event_id FROM career_events
                    WHERE career_event_id='20000000-0000-4000-8000-000000000002'"""), "20000000-0000-4000-8000-000000000001"),
                _check("Cross-type supersession omitted", _scalar(connection, """SELECT supersedes_event_id FROM career_events
                    WHERE career_event_id='20000000-0000-4000-8000-000000000003'"""), None),
                _check("No orphan semantic links", _scalar(connection, """SELECT count(*) FROM semantic_memories n LEFT JOIN semantic_memories o
                    ON o.semantic_memory_id=n.supersedes_semantic_memory_id
                    WHERE n.supersedes_semantic_memory_id IS NOT NULL AND o.semantic_memory_id IS NULL"""), 0),
                _check("No orphan event links", _scalar(connection, """SELECT count(*) FROM career_events n LEFT JOIN career_events o
                    ON o.career_event_id=n.supersedes_event_id
                    WHERE n.supersedes_event_id IS NOT NULL AND o.career_event_id IS NULL"""), 0),
                _check("No approved_memory corpus remains", list(corpus.values()).count("approved_memory"), 0),
                _check("Semantic corpus conversion", sum(corpus[i] == "semantic_memory" for i in expected_semantic_ids), 4),
                _check("Episodic corpus conversion", sum(corpus[i] == "episodic_event" for i in expected_event_ids), 3),
            ]
            jobs = _schema_jobs(connection)
            failed_jobs = [j for j in jobs if str(j.get("status", "")).casefold() != "succeeded"]
            checks.append(("Target schema-change jobs found", bool(jobs), len(jobs), "> 0"))
            checks.append(_check("Matching schema-change jobs succeeded", len(failed_jobs), 0))
            return checks, jobs
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-name", help=f"Optional name beginning with {DATABASE_PREFIX}")
    args = parser.parse_args()
    raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        print("FAIL: DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    try:
        base_url = make_url(raw_url)
        database = _safe_name(args.database_name)
        admin_url = _url_for_database(base_url, "defaultdb")
        validation_url = _url_for_database(base_url, database)
        print(f"Disposable database: {database}")
        print("Administrative database: defaultdb")
        print("Credentials/URL: [not printed]")
        _create_database(admin_url, database)
        base_seconds = _alembic_upgrade(validation_url, BASE_REVISION)
        engine = create_engine(validation_url)
        try:
            with engine.connect() as connection:
                current = _scalar(connection, "SELECT version_num FROM alembic_version")
        finally:
            engine.dispose()
        if current != BASE_REVISION:
            raise ValidationFailure(f"Expected {BASE_REVISION}, found {current!r}")
        print(f"Pre-migration revision: {current} (PASS)")
        _insert_legacy_fixture(validation_url)
        migration_seconds = _alembic_upgrade(validation_url, TARGET_REVISION)
        checks, jobs = _validate(validation_url)
        print("\nValidation checks:")
        failed = []
        for name, passed, actual, expected in checks:
            print(f"  {'PASS' if passed else 'FAIL'}: {name}")
            print(f"        actual={actual!r}; expected={expected!r}")
            if not passed:
                failed.append(name)
        if jobs:
            print("\nMatching Cockroach schema-change jobs:")
            for job in jobs:
                print(f"  job_id={job.get('job_id')} status={job.get('status')}")
        print(f"\nMigrations to {BASE_REVISION}: {base_seconds:.3f}s")
        print(f"Migration {BASE_REVISION} -> {TARGET_REVISION}: {migration_seconds:.3f}s")
        print(f"Matching Cockroach schema-change jobs inspected: {len(jobs)}")
        print(f"Disposable database retained for inspection: {database}")
        if failed:
            print(f"\nRESULT: FAIL ({len(failed)} failed checks)")
            return 1
        print("\nRESULT: PASS")
        return 0
    except Exception as exc:
        print(f"\nRESULT: FAIL — {type(exc).__name__}: {exc}", file=sys.stderr)
        print("No credentials were printed. Any created validation database is retained for inspection.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
