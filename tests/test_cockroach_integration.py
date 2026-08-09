import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.database.database import PROJECT_ROOT, create_database_engine


@unittest.skipUnless(os.getenv("COCKROACH_TEST_DATABASE_URL"), "isolated Cockroach test URL not configured")
class CockroachIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["COCKROACH_TEST_DATABASE_URL"]
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", cls.url)
        command.upgrade(config, "head")
        cls.engine = create_database_engine(cls.url)

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
