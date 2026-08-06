import unittest
from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.evidence import EvidenceService, sanitize_external_content


class FakeStorage:
    def __init__(self): self.objects = {}
    def put(self, key, data, content_type): self.objects[key] = (data, content_type)
    def get(self, key): return self.objects[key][0]
    def delete(self, key): self.objects.pop(key, None)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        conversation = self.repository.create_conversation(self.user["user_id"], "Agent")
        self.run = self.repository.create_agent_run(self.user["user_id"], conversation["conversation_id"], goal="Search")
        self.storage = FakeStorage()
        self.service = EvidenceService(self.repository, self.storage)

    def tearDown(self): self.engine.dispose()

    def test_small_sql_and_large_s3_evidence(self):
        with patch.dict("os.environ", {"EVIDENCE_S3_ENABLED": "true", "EVIDENCE_S3_THRESHOLD_BYTES": "20"}):
            small, _ = self.service.store(user_id=self.user["user_id"], run_id=self.run["run_id"], source_type="job", source_name="Official", source_url="https://example.com", content_type="text/plain", raw_content="small")
            large, _ = self.service.store(user_id=self.user["user_id"], run_id=self.run["run_id"], source_type="job", source_name="Official", source_url="https://example.com", content_type="text/html", raw_content="<script>bad()</script>" + "public data " * 20)
        self.assertEqual(small["storage_backend"], "sql")
        self.assertEqual(large["storage_backend"], "s3")
        self.assertNotIn("bad()", large["content_excerpt"])
        self.assertTrue(self.storage.objects)

    def test_cross_user_evidence_denied(self):
        item, _ = self.service.store(user_id=self.user["user_id"], run_id=self.run["run_id"], source_type="job", source_name="Official", source_url=None, content_type="text/plain", raw_content="data")
        other = self.repository.get_or_create_user("Other", "other@example.com")
        with self.assertRaisesRegex(ValueError, "not found"):
            self.repository.get_evidence(other["user_id"], item["evidence_id"])


if __name__ == "__main__":
    unittest.main()
