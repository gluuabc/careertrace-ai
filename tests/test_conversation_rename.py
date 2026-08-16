import unittest

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository


class ConversationRenameTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.other = self.repository.get_or_create_user("Grace", "grace@example.com")
        self.conversation = self.repository.create_conversation(
            self.user["user_id"], "Original title"
        )
        self.message = self.repository.add_message(
            self.user["user_id"],
            self.conversation["conversation_id"],
            "user",
            "Keep this message",
        )

    def tearDown(self):
        self.engine.dispose()

    def test_conversation_rename_is_user_scoped(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            self.repository.rename_conversation(
                self.other["user_id"],
                self.conversation["conversation_id"],
                "Unauthorized",
            )
        current = self.repository.get_conversation(
            self.user["user_id"], self.conversation["conversation_id"]
        )
        self.assertEqual(current["title"], "Original title")

    def test_conversation_rename_preserves_messages(self):
        renamed = self.repository.rename_conversation(
            self.user["user_id"], self.conversation["conversation_id"], "New title"
        )
        current = self.repository.get_conversation(
            self.user["user_id"], self.conversation["conversation_id"]
        )
        self.assertEqual(renamed["conversation_id"], self.conversation["conversation_id"])
        self.assertEqual(current["title"], "New title")
        self.assertEqual(current["messages"][0]["message_id"], self.message["message_id"])


if __name__ == "__main__":
    unittest.main()
