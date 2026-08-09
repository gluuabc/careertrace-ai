import hashlib
import json
import unittest

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.graph.career_agent_graph import CareerAgentGraph
from app.prompts import build_system_prompt
from app.services.context_manager import ContextManager
from app.services.errors import sanitize_diagnostic
from app.services.skill_registry import SkillRegistry
from app.tools import CAREER_AGENT_TOOLS


class AgentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.conversation = self.repository.create_conversation(self.user["user_id"], "Agent")
        self.registry = SkillRegistry()

    def tearDown(self):
        self.engine.dispose()

    def test_classifier_retries_twice_and_records_safe_fallback(self):
        from tests.test_agent_graph import _FailingClassifier

        model = _FailingClassifier()
        graph = CareerAgentGraph(
            self.repository,
            ContextManager(self.repository, self.registry),
            self.registry,
            model_factory=lambda _: model,
        )
        run = self.repository.create_agent_run(
            self.user["user_id"], self.conversation["conversation_id"], goal="Find internships"
        )
        result = graph.classify_intent(
            {
                "user_id": self.user["user_id"],
                "conversation_id": self.conversation["conversation_id"],
                "run_id": run["run_id"],
                "current_request": "Find internships",
                "warnings": [],
            }
        )
        self.assertEqual(model.calls, 2)
        self.assertEqual(result["routing_source"], "deterministic_fallback")
        stored = self.repository.list_agent_runs(self.user["user_id"], self.conversation["conversation_id"])[0]
        self.assertNotIn("secret-token", json.dumps(stored))

    def test_static_prompt_and_ordered_tool_hashes_are_user_independent(self):
        prompt_hashes = []
        tool_hashes = []
        for _user in ("one", "two"):
            prompt_hashes.append(hashlib.sha256(build_system_prompt(self.registry.catalog()).encode()).hexdigest())
            definitions = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "schema": tool.tool_call_schema.model_json_schema(),
                }
                for tool in CAREER_AGENT_TOOLS
            ]
            tool_hashes.append(hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        self.assertEqual(len(set(prompt_hashes)), 1)
        self.assertEqual(len(set(tool_hashes)), 1)

    def test_diagnostics_redact_secrets_and_tokenized_urls(self):
        value = sanitize_diagnostic(
            "Authorization: BearerSecret https://user:pass@example.com/x?token=abc"
        )
        self.assertNotIn("BearerSecret", value)
        self.assertNotIn("pass", value)
        self.assertNotIn("token=abc", value)


if __name__ == "__main__":
    unittest.main()
