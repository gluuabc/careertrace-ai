import unittest

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository


class ProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user(
            "Ada Student", "ada@example.com"
        )

    def tearDown(self):
        self.engine.dispose()

    def test_profile_crud_and_analysis_history(self):
        profile = {
            "name": "Ada Student",
            "email": "ada@example.com",
            "education": ["B.S. Computer Science"],
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "career_goal": "Build reliable AI systems",
            "skills": ["Python", "Python", "SQL"],
            "projects": [
                {"title": "CareerTrace", "description": "Career profile system"}
            ],
            "experience": [
                {
                    "organization": "Example Lab",
                    "role": "Research Assistant",
                    "description": "Evaluated language models",
                }
            ],
            "target_roles": ["ML Engineer"],
            "preferred_locations": ["California"],
            "employment_types": ["Internship"],
            "work_authorization": "Authorized",
            "remote_preference": "Hybrid",
        }

        saved = self.repository.upsert_profile(self.user["user_id"], profile)
        self.assertEqual(saved["profile_version"], 1)
        self.assertEqual(saved["skills"], ["Python", "SQL"])
        self.assertEqual(saved["projects"][0]["title"], "CareerTrace")

        analysis = self.repository.save_analysis(
            self.user["user_id"],
            {
                "strengths": ["AI systems"],
                "possible_roles": ["ML Engineer Intern"],
                "recommended_next_skills": ["MLOps"],
            },
        )
        self.assertEqual(analysis["profile_version"], 1)

        profile["graduation_year"] = 2029
        updated = self.repository.upsert_profile(self.user["user_id"], profile)
        self.assertEqual(updated["profile_version"], 2)
        self.assertEqual(updated["graduation_year"], 2029)

        latest = self.repository.get_latest_analysis(self.user["user_id"])
        self.assertEqual(latest["analysis_id"], analysis["analysis_id"])
        self.assertLess(latest["profile_version"], updated["profile_version"])


if __name__ == "__main__":
    unittest.main()
