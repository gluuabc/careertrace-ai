import unittest

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository


class StarredQATests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.other = self.repository.get_or_create_user("Other", "other@example.com")
        self.conversation = self.repository.create_conversation(
            self.user["user_id"], "Internship planning"
        )
        self.question = self.repository.add_message(
            self.user["user_id"], self.conversation["conversation_id"], "user", "What next?"
        )
        self.answer = self.repository.add_message(
            self.user["user_id"],
            self.conversation["conversation_id"],
            "assistant",
            "Build one relevant project.",
            reply_to_message_id=self.question["message_id"],
        )

    def tearDown(self):
        self.engine.dispose()

    def test_star_persists_duplicate_is_idempotent_and_unstar_works(self):
        item = self.repository.star_qa_pair(
            self.user["user_id"],
            self.conversation["conversation_id"],
            self.question["message_id"],
            self.answer["message_id"],
        )
        duplicate = self.repository.star_qa_pair(
            self.user["user_id"],
            self.conversation["conversation_id"],
            self.question["message_id"],
            self.answer["message_id"],
        )
        reloaded = ProfileRepository(create_session_factory(self.engine))
        self.assertEqual(item["starred_qa_id"], duplicate["starred_qa_id"])
        self.assertEqual(reloaded.list_starred_qa_pairs(self.user["user_id"])[0]["question"], "What next?")
        reloaded.unstar_qa_pair(self.user["user_id"], item["starred_qa_id"])
        self.assertEqual(reloaded.list_starred_qa_pairs(self.user["user_id"]), [])

    def test_pairing_and_user_scope_are_enforced(self):
        other_conversation = self.repository.create_conversation(
            self.user["user_id"], "Other"
        )
        other_question = self.repository.add_message(
            self.user["user_id"], other_conversation["conversation_id"], "user", "Other?"
        )
        with self.assertRaises(ValueError):
            self.repository.star_qa_pair(
                self.user["user_id"],
                self.conversation["conversation_id"],
                other_question["message_id"],
                self.answer["message_id"],
            )
        with self.assertRaises(ValueError):
            self.repository.star_qa_pair(
                self.other["user_id"],
                self.conversation["conversation_id"],
                self.question["message_id"],
                self.answer["message_id"],
            )

    def test_star_and_preference_actions_are_independent(self):
        profile = {
            "school": "Example",
            "major": "CS",
            "graduation_year": 2028,
            "skills": ["Python"],
            "experience": [{"role": "Intern"}],
            "target_roles": ["Engineer"],
        }
        self.repository.upsert_profile(self.user["user_id"], profile)
        before = self.repository.get_profile(self.user["user_id"])
        starred = self.repository.star_qa_pair(
            self.user["user_id"],
            self.conversation["conversation_id"],
            self.question["message_id"],
            self.answer["message_id"],
            preference_update_summary="Target roles: Engineer → ML Engineer",
        )
        after = self.repository.get_profile(self.user["user_id"])
        self.assertEqual(before["target_roles"], after["target_roles"])
        self.assertIn("ML Engineer", starred["preference_update_summary"])
        profile["target_roles"] = ["ML Engineer"]
        self.repository.upsert_profile(self.user["user_id"], profile)
        self.assertEqual(len(self.repository.list_starred_qa_pairs(self.user["user_id"])), 1)

    def test_star_order_and_analysis_history_remain_intact(self):
        profile = self.repository.upsert_profile(
            self.user["user_id"],
            {
                "school": "Example",
                "major": "CS",
                "graduation_year": 2028,
                "skills": ["Python"],
                "experience": [{"role": "Intern"}],
            },
        )
        self.repository.save_analysis(
            self.user["user_id"],
            {"strengths": ["Python"], "possible_roles": [], "recommended_next_skills": []},
        )
        self.repository.star_qa_pair(
            self.user["user_id"],
            self.conversation["conversation_id"],
            self.question["message_id"],
            self.answer["message_id"],
        )
        q2 = self.repository.add_message(
            self.user["user_id"], self.conversation["conversation_id"], "user", "And then?"
        )
        a2 = self.repository.add_message(
            self.user["user_id"],
            self.conversation["conversation_id"],
            "assistant",
            "Apply the learning.",
            reply_to_message_id=q2["message_id"],
        )
        self.repository.star_qa_pair(
            self.user["user_id"], self.conversation["conversation_id"], q2["message_id"], a2["message_id"]
        )
        self.assertEqual(self.repository.list_starred_qa_pairs(self.user["user_id"])[0]["question"], "And then?")
        self.assertEqual(len(self.repository.list_analysis_versions(self.user["user_id"])), 1)
        self.assertEqual(profile["profile_version"], 1)


if __name__ == "__main__":
    unittest.main()
