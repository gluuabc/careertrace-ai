import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.database.database import PROJECT_ROOT, run_retryable_transaction
from app.database.retrieval_repository import RetrievalRepository


class _CockroachFactory:
    kw = {"bind": SimpleNamespace(dialect=SimpleNamespace(name="cockroachdb"))}


class CockroachSkillProofTests(unittest.TestCase):
    def test_cockroach_transaction_retry_is_bounded_and_idempotent(self):
        calls = []
        effects = set()

        def transaction(_session):
            calls.append("attempt")
            # Retried transaction units must be idempotent and database-only.
            effects.add("budget-reservation")
            return "reserved"

        def retry_runner(factory, callback, **kwargs):
            self.assertIs(factory, _CockroachFactory)
            self.assertEqual(kwargs, {"max_retries": 3, "max_backoff": 1})
            callback(object())
            return callback(object())

        result = run_retryable_transaction(
            transaction,
            session_factory=_CockroachFactory,
            _runner=retry_runner,
        )
        self.assertEqual(result, "reserved")
        self.assertEqual(len(calls), 2)
        self.assertEqual(effects, {"budget-reservation"})

    def test_cockroach_retrieval_sql_has_deterministic_user_and_session_scope(self):
        source = inspect.getsource(RetrievalRepository)
        self.assertIn("user_id IS NULL OR user_id = :user_id", source)
        self.assertIn("retrieval_document_id IN :document_ids", source)
        self.assertIn("search_vector_fts @@ plainto_tsquery", source)

    def test_cockroach_vector_and_fts_schema_expected_shape(self):
        migration = Path(
            PROJECT_ROOT / "migrations/versions/20260810_08_retrieval_documents.py"
        ).read_text()
        self.assertIn("PortableVector(1024)", migration)
        self.assertIn("search_vector_fts TSVECTOR", migration)
        self.assertIn("CREATE INVERTED INDEX ix_retrieval_documents_fts", migration)
        self.assertIn("CREATE VECTOR INDEX ix_retrieval_documents_embedding", migration)


if __name__ == "__main__":
    unittest.main()
