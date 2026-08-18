import unittest
from pathlib import Path

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository


def _profile(major: str) -> dict:
    return {
        "school": "Northstar Institute of Technology",
        "major": major,
        "graduation_year": 2028,
        "skills": ["Python"],
        "experience": [{"role": "Student Research Assistant"}],
    }


class JudgeDemoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))

    def tearDown(self):
        self.engine.dispose()

    def test_each_judge_session_gets_a_distinct_empty_uuid_user(self):
        first = self.repository.create_demo_user()
        second = self.repository.create_demo_user()

        self.assertNotEqual(first["user_id"], second["user_id"])
        self.assertTrue(first["is_demo"])
        self.assertIsNone(self.repository.get_profile(first["user_id"]))
        self.assertIsNone(self.repository.get_latest_analysis(first["user_id"]))

        shared_synthetic_identity = {
            **_profile("Computer Science"),
            "name": "Maya Chen",
            "email": "maya.chen.demo@example.com",
        }
        self.repository.upsert_profile(first["user_id"], shared_synthetic_identity)
        self.repository.upsert_profile(second["user_id"], shared_synthetic_identity)
        self.assertIsNone(self.repository.get_user(first["user_id"])["email"])
        self.assertEqual(
            self.repository.get_profile(first["user_id"])["email"],
            "maya.chen.demo@example.com",
        )

    def test_judge_uses_normal_profile_and_analysis_repository_workflow(self):
        judge = self.repository.create_demo_user()
        saved = self.repository.upsert_profile(judge["user_id"], _profile("CS"))
        analysis = self.repository.save_analysis(
            judge["user_id"],
            {
                "strengths": ["Python"],
                "possible_roles": ["Engineer"],
                "recommended_next_skills": ["MLOps"],
            },
        )

        self.assertEqual(saved["profile_version"], 1)
        self.assertEqual(analysis["profile_version_used"], 1)
        self.assertEqual(
            self.repository.get_profile(judge["user_id"])["major"], "CS"
        )

    def test_judge_cannot_resolve_another_user_as_its_demo_identity(self):
        judge = self.repository.create_demo_user()
        other = self.repository.get_or_create_user("Real", "real@example.com")

        self.assertEqual(
            self.repository.get_demo_user(judge["user_id"])["user_id"],
            judge["user_id"],
        )
        with self.assertRaisesRegex(ValueError, "not available"):
            self.repository.get_demo_user(other["user_id"])

    def test_synthetic_alumni_csv_is_packaged_for_judge_download(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "demo"
            / "Example_Alumni_Connections.csv"
        )
        content = fixture.read_text(encoding="utf-8")

        self.assertIn("name,education,organization,role,public_profile_url", content)
        self.assertIn("Northstar Institute of Technology", content)
        self.assertIn("https://example.com/", content)


if __name__ == "__main__":
    unittest.main()
