import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from langchain_core.messages import AIMessage

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.prompts import build_system_prompt
from app.services.context_manager import ContextManager
from app.services.skill_registry import SkillRegistry


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.conversation = self.repository.create_conversation(self.user["user_id"], "Agent")
        self.registry = SkillRegistry()
        self.manager = ContextManager(self.repository, self.registry)

    def tearDown(self):
        self.engine.dispose()

    def test_static_prompt_contains_no_profile_and_does_not_change_with_profile(self):
        before = build_system_prompt(self.registry.catalog())
        self.repository.upsert_profile(self.user["user_id"], {"school": "Secret University", "major": "CS", "graduation_year": 2028, "skills": ["Python"], "experience": [{"role": "Intern"}]})
        after = build_system_prompt(self.registry.catalog())
        self.assertEqual(before, after)
        self.assertNotIn("Secret University", before)

    def test_one_runtime_block_and_current_request_is_last(self):
        self.repository.add_message(self.user["user_id"], self.conversation["conversation_id"], "user", "Earlier")
        self.repository.add_message(self.user["user_id"], self.conversation["conversation_id"], "assistant", "Earlier reply")
        self.repository.add_message(self.user["user_id"], self.conversation["conversation_id"], "user", "Find internships")
        messages = self.manager.build_messages(user_id=self.user["user_id"], conversation_id=self.conversation["conversation_id"], current_request="Find internships", agent_status={"current_step": "search"})
        self.assertEqual(sum("<runtime_context>" in str(item.content) for item in messages), 1)
        self.assertEqual(messages[-1].content, "Find internships")

    def test_context_threshold_uses_configured_limit(self):
        with patch.dict(os.environ, {"CONTEXT_MODEL_LIMIT_TOKENS": "10000", "CONTEXT_RESERVED_OUTPUT_TOKENS": "1000", "CONTEXT_SAFETY_MARGIN_TOKENS": "1000", "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.8"}):
            self.assertEqual(self.manager.compression_threshold(), 6400)

    def test_compression_preserves_original_messages_and_boundary(self):
        for index in range(6):
            self.repository.add_message(
                self.user["user_id"],
                self.conversation["conversation_id"],
                "user" if index % 2 == 0 else "assistant",
                f"message {index} " + "x" * 1200,
            )
        current = "current request " + "y" * 1200
        self.repository.add_message(
            self.user["user_id"], self.conversation["conversation_id"], "user", current
        )
        llm = Mock()
        llm.invoke.return_value = AIMessage(
            content="Summary preserves [EVIDENCE: ev_123] and constraints."
        )
        with (
            patch.dict(
                os.environ,
                {
                    "CONTEXT_MODEL_LIMIT_TOKENS": "2500",
                    "CONTEXT_RESERVED_OUTPUT_TOKENS": "500",
                    "CONTEXT_SAFETY_MARGIN_TOKENS": "200",
                    "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.8",
                },
            ),
            patch("app.services.context_manager.get_llm", return_value=llm),
        ):
            messages = self.manager.build_messages(
                user_id=self.user["user_id"],
                conversation_id=self.conversation["conversation_id"],
                current_request=current,
            )
        self.assertEqual(messages[-1].content, current)
        self.assertIsNotNone(
            self.repository.get_latest_context_summary(
                self.user["user_id"], self.conversation["conversation_id"]
            )
        )
        self.assertEqual(
            len(
                self.repository.get_conversation(
                    self.user["user_id"], self.conversation["conversation_id"]
                )["messages"]
            ),
            7,
        )


if __name__ == "__main__":
    unittest.main()
