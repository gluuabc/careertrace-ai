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
        self.assertTrue(saved["profile_changed"])
        self.assertEqual(saved["skills"], ["Python", "SQL"])
        self.assertEqual(saved["projects"][0]["title"], "CareerTrace")

        unchanged = self.repository.upsert_profile(self.user["user_id"], profile)
        self.assertEqual(unchanged["profile_version"], 1)
        self.assertFalse(unchanged["profile_changed"])

        analysis = self.repository.save_analysis(
            self.user["user_id"],
            {
                "strengths": ["AI systems"],
                "possible_roles": ["ML Engineer Intern"],
                "recommended_next_skills": ["MLOps"],
            },
        )
        self.assertEqual(analysis["profile_version_used"], 1)
        self.assertFalse(analysis["is_stale"])

        profile["graduation_year"] = 2029
        updated = self.repository.upsert_profile(self.user["user_id"], profile)
        self.assertEqual(updated["profile_version"], 2)
        self.assertEqual(updated["graduation_year"], 2029)

        latest = self.repository.get_latest_analysis(self.user["user_id"])
        self.assertEqual(latest["analysis_id"], analysis["analysis_id"])
        self.assertEqual(latest["profile_version_used"], 1)
        self.assertTrue(latest["is_stale"])

    def test_rejects_missing_previous_and_new_required_fields(self):
        with self.assertRaisesRegex(
            ValueError,
            "school, major, graduation_year, skills, experience",
        ):
            self.repository.upsert_profile(
                self.user["user_id"],
                {
                    "school": None,
                    "major": None,
                    "graduation_year": None,
                    "skills": [],
                    "experience": [],
                },
            )

    def test_document_metadata_is_scoped_to_its_owner(self):
        other_user = self.repository.get_or_create_user(
            "Grace Student", "grace@example.com"
        )
        document = self.repository.create_document(
            document_id="document-1",
            user_id=self.user["user_id"],
            filename="resume.pdf",
            s3_key=f"{self.user['user_id']}/document-1/resume.pdf",
            document_type="resume",
            content_type="application/pdf",
            size_bytes=8,
        )

        self.assertEqual(
            self.repository.get_document(
                self.user["user_id"], document["document_id"]
            )["filename"],
            "resume.pdf",
        )
        with self.assertRaisesRegex(ValueError, "not found"):
            self.repository.get_document(
                other_user["user_id"], document["document_id"]
            )

    def test_google_identity_links_existing_email_to_uuid(self):
        linked = self.repository.get_or_create_google_user(
            google_id="google-subject-1",
            email="ADA@EXAMPLE.COM",
            name="Ada Lovelace",
            profile_image="https://example.com/ada.png",
        )
        repeated = self.repository.get_or_create_google_user(
            google_id="google-subject-1",
            email="ada@example.com",
            name="Ada Lovelace",
        )

        self.assertEqual(linked["user_id"], self.user["user_id"])
        self.assertEqual(repeated["user_id"], self.user["user_id"])
        self.assertEqual(linked["google_id"], "google-subject-1")
        self.assertEqual(linked["email"], "ada@example.com")


if __name__ == "__main__":
    unittest.main()
