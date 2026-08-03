import unittest

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.services.demo import (
    DEMO_PROFILE,
    DEMO_USER_ID,
    reset_demo_data,
)


class JudgeDemoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))

    def tearDown(self):
        self.engine.dispose()

    def test_judge_mode_loads_fixed_synthetic_demo_user(self):
        user = reset_demo_data(self.repository)
        profile = self.repository.get_profile(DEMO_USER_ID)
        analysis = self.repository.get_latest_analysis(DEMO_USER_ID)

        self.assertEqual(user["user_id"], DEMO_USER_ID)
        self.assertTrue(user["is_demo"])
        self.assertIsNone(user["email"])
        self.assertEqual(profile["school"], DEMO_PROFILE["school"])
        self.assertTrue(profile["skills"])
        self.assertIsNotNone(analysis)

    def test_judge_identity_cannot_be_resolved_as_another_user(self):
        reset_demo_data(self.repository)
        real_user = self.repository.get_or_create_user(
            "Real Student", "real@example.com"
        )
        self.repository.upsert_profile(
            real_user["user_id"],
            {
                "school": "Real University",
                "major": "History",
                "graduation_year": 2027,
                "skills": ["Research"],
                "experience": [{"role": "Tutor"}],
            },
        )

        demo_user = self.repository.get_demo_user(DEMO_USER_ID)
        demo_profile = self.repository.get_profile(demo_user["user_id"])

        self.assertEqual(demo_profile["school"], DEMO_PROFILE["school"])
        self.assertNotEqual(demo_profile["school"], "Real University")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.repository.get_demo_user(real_user["user_id"])

    def test_reset_restores_seeded_profile_analysis_and_empty_documents(self):
        reset_demo_data(self.repository)
        changed = {**DEMO_PROFILE, "major": "Changed Major", "skills": ["Changed"]}
        self.repository.upsert_profile(DEMO_USER_ID, changed)
        self.repository.save_analysis(
            DEMO_USER_ID,
            {
                "strengths": ["Changed"],
                "possible_roles": ["Changed"],
                "recommended_next_skills": ["Changed"],
            },
        )
        self.repository.create_document(
            document_id="demo-document",
            user_id=DEMO_USER_ID,
            filename="synthetic.pdf",
            s3_key=f"{DEMO_USER_ID}/demo-document/synthetic.pdf",
            document_type="resume",
            content_type="application/pdf",
            size_bytes=10,
        )

        reset_demo_data(self.repository)
        restored = self.repository.get_profile(DEMO_USER_ID)
        analysis = self.repository.get_latest_analysis(DEMO_USER_ID)

        self.assertEqual(restored["major"], DEMO_PROFILE["major"])
        self.assertEqual(restored["skills"], DEMO_PROFILE["skills"])
        self.assertEqual(restored["profile_version"], 1)
        self.assertNotEqual(analysis["strengths"], ["Changed"])
        self.assertEqual(self.repository.list_documents(DEMO_USER_ID), [])


if __name__ == "__main__":
    unittest.main()
