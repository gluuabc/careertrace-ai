import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy_cockroachdb import run_transaction

from app.database.database import PROJECT_ROOT, create_database_engine, create_session_factory
from app.database.repository import ProfileRepository
from app.database.retrieval_repository import RetrievalRepository


@unittest.skipUnless(os.getenv("COCKROACH_TEST_DATABASE_URL"), "isolated Cockroach test URL not configured")
class CockroachIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["COCKROACH_TEST_DATABASE_URL"]
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", cls.url.replace("%", "%%"))
        migration = os.getenv("COCKROACH_LIVE_RUN_MIGRATIONS", "false").casefold() == "true"
        if migration:
            with patch.dict(os.environ, {"DATABASE_URL": cls.url}):
                command.upgrade(config, "head")
        cls.engine = create_database_engine(cls.url)
        cls.factory = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_required_state_and_retrieval_tables_migrated(self):
        tables = set(inspect(self.engine).get_table_names())
        self.assertTrue({"profiles", "agent_runs", "search_sessions", "retrieval_documents"} <= tables)

    def test_database_is_cockroach_and_readable(self):
        with self.engine.connect() as connection:
            version = str(connection.scalar(text("SELECT version()")))
        self.assertIn("CockroachDB", version)

    def test_vector_fts_and_visibility_indexes_are_live(self):
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SHOW COLUMNS FROM retrieval_documents")
            ).fetchall()

        columns = {row[0]: row[1] for row in rows}
        indexes = {item["name"] for item in inspect(self.engine).get_indexes("retrieval_documents")}

        print("DATABASE:", self.engine.url)
        print("COLUMNS:", columns)
        print("INDEXES:", indexes)

        self.assertEqual(columns["embedding"], "VECTOR(1024)")
        self.assertIn("search_vector_fts", columns)
        self.assertTrue({"ix_retrieval_documents_fts", "ix_retrieval_documents_embedding", "ix_retrieval_documents_visibility"} <= indexes)

    def test_live_vector_fts_repository_queries_and_explain(self):
        repository = ProfileRepository(self.factory)
        retrieval = RetrievalRepository(self.factory)
        user = repository.get_or_create_user("Cockroach Skill Test")
        other = repository.get_or_create_user("Cockroach Skill Test Other")
        vector = [1.0, *([0.0] * 1023)]
        own = retrieval.upsert_document(
            corpus_type="project", user_id=user["user_id"], source_entity_id=f"live-{uuid4()}",
            source_version="1", title="Distributed systems", text_content="Cockroach vector database project",
            embedding_model_id="test", embedding_dimension=1024, embedding=vector,
        )
        retrieval.upsert_document(
            corpus_type="project", user_id=other["user_id"], source_entity_id=f"live-{uuid4()}",
            source_version="1", title="Private other", text_content="Cockroach vector database project",
            embedding_model_id="test", embedding_dimension=1024, embedding=vector,
        )
        sparse = retrieval.sparse_search(user["user_id"], "Cockroach", ["project"])
        dense = retrieval.dense_search(user["user_id"], vector, ["project"])
        self.assertEqual({item[0]["retrieval_document_id"] for item in sparse}, {own["retrieval_document_id"]})
        self.assertEqual({item[0]["retrieval_document_id"] for item in dense}, {own["retrieval_document_id"]})
        with self.engine.connect() as connection:
            sparse_plan = "\n".join(str(row[0]) for row in connection.execute(text(
                "EXPLAIN SELECT retrieval_document_id FROM retrieval_documents "
                "WHERE user_id = :user_id AND corpus_type = 'project' AND active = true "
                "AND search_vector_fts @@ plainto_tsquery('english', 'Cockroach') LIMIT 5"
            ), {"user_id": user["user_id"]}))
            vector_plan = "\n".join(str(row[0]) for row in connection.execute(text(
                "EXPLAIN SELECT retrieval_document_id FROM retrieval_documents "
                "WHERE user_id = :user_id AND corpus_type = 'project' AND active = true "
                "ORDER BY embedding <=> CAST(:embedding AS VECTOR) LIMIT 5"
            ), {"user_id": user["user_id"], "embedding": str(vector)}))
        self.assertTrue(sparse_plan.strip())
        self.assertTrue(vector_plan.strip())

    def test_cockroach_serialization_retry_does_not_duplicate_effect(self):
        user_id = str(uuid4())
        attempts = []

        def transaction(session):
            attempts.append(1)
            session.execute(text(
                "INSERT INTO users (user_id, name, is_demo, created_at, updated_at) "
                "VALUES (:user_id, 'Retry proof', false, now(), now()) ON CONFLICT (user_id) DO NOTHING"
            ), {"user_id": user_id})

        try:
            run_transaction(
                self.factory, transaction, max_retries=3, max_backoff=1,
                inject_error=True,
            )
            with self.engine.connect() as connection:
                count = connection.scalar(text("SELECT count(*) FROM users WHERE user_id = :user_id"), {"user_id": user_id})
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(count, 1)
        finally:
            with self.engine.begin() as connection:
                connection.execute(text("DELETE FROM users WHERE user_id = :user_id"), {"user_id": user_id})

    def test_live_source_budget_reservation_is_atomic(self):
        repository = ProfileRepository(self.factory)
        user = repository.get_or_create_user("Cockroach Budget Test")
        conversation = repository.create_conversation(user["user_id"], "Cockroach budget")
        run = repository.create_agent_run(user["user_id"], conversation["conversation_id"], goal="Budget")
        search = repository.get_or_create_search_session(
            user["user_id"], run["run_id"], intent="job_search", normalized_request={},
            requested_count=5, source_call_budget=3,
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            reservations = list(pool.map(
                lambda _: repository.reserve_search_source_calls(user["user_id"], search["search_session_id"], 1)["reserved_calls"],
                range(10),
            ))
        self.assertEqual(sum(reservations), 3)
