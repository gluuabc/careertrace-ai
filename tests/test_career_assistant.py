import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.services.career_assistant import respond_to_user


class CareerAssistantTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.repository.upsert_profile(
            self.user["user_id"],
            {
                "school": "Example University",
                "major": "Computer Science",
                "graduation_year": 2028,
                "skills": ["Python"],
                "experience": [{"role": "Intern"}],
            },
        )
        self.conversation = self.repository.create_conversation(
            self.user["user_id"], "Career plan"
        )

    def tearDown(self):
        self.engine.dispose()

    def test_chat_persists_both_messages_without_modifying_memory_or_profile(self):
        llm = Mock()
        llm.invoke.return_value = AIMessage(content="Consider an ML internship.")
        before_versions = self.repository.list_profile_versions(
            self.user["user_id"]
        )

        with patch("app.services.career_assistant.get_llm", return_value=llm):
            response = respond_to_user(
                self.user["user_id"],
                self.conversation["conversation_id"],
                "What role should I explore?",
                self.repository,
            )

        stored = self.repository.get_conversation(
            self.user["user_id"], self.conversation["conversation_id"]
        )
        self.assertEqual(response, "Consider an ML internship.")
        self.assertEqual(
            [message["role"] for message in stored["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            len(self.repository.list_profile_versions(self.user["user_id"])),
            len(before_versions),
        )
        self.assertEqual(
            self.repository.list_memory_candidates(self.user["user_id"]), []
        )


if __name__ == "__main__":
    unittest.main()
