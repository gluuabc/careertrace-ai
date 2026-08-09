import unittest
import json

from app.graph.career_agent_graph import CareerAgentGraph, _fallback_intent
from app.state.agent_schema import CareerIntent, IntentDecision
from langchain_core.messages import AIMessage, ToolMessage
from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.context_manager import ContextManager
from app.services.skill_registry import SkillRegistry
from app.tools.jobs import search_jobs
from app.tools.people import search_people


class AgentGraphTests(unittest.TestCase):
    def test_closed_intents_route_action_requests(self):
        self.assertEqual(_fallback_intent("Find software internships").intent, CareerIntent.JOB_SEARCH)
        self.assertEqual(_fallback_intent("Find a professor researching HCI").intent, CareerIntent.PEOPLE_SEARCH)
        self.assertEqual(_fallback_intent("Tailor my resume").intent, CareerIntent.RESUME_REVISION)
        self.assertEqual(_fallback_intent("Draft outreach to this alumnus").intent, CareerIntent.OUTREACH)

    def test_model_tool_schema_cannot_choose_user_identity(self):
        self.assertEqual(set(search_jobs.tool_call_schema.model_json_schema()["properties"]), {"request"})
        self.assertEqual(set(search_people.tool_call_schema.model_json_schema()["properties"]), {"request"})

    def test_sufficient_results_and_budgets_stop_the_loop(self):
        self.assertEqual(
            CareerAgentGraph._route_after_model(None, {"is_sufficient": True}),
            "final",
        )
        self.assertEqual(
            CareerAgentGraph._route_after_model(
                None, {"total_source_calls": 12, "messages": []}
            ),
            "final",
        )
        self.assertEqual(
            CareerAgentGraph._route_after_model(
                None, {"consecutive_no_new_results": 2, "messages": []}
            ),
            "final",
        )

    def test_needs_input_bypasses_workflow_preparation(self):
        self.assertEqual(
            CareerAgentGraph._route_after_classify({"needs_user_input": True}),
            "clarify",
        )
        self.assertEqual(
            CareerAgentGraph._route_after_classify({"needs_user_input": False}),
            "prepare",
        )

    def test_disallowed_and_exhausted_calls_receive_matching_tool_messages(self):
        engine = create_database_engine("sqlite://")
        init_db(engine)
        repository = ProfileRepository(create_session_factory(engine))
        user = repository.get_or_create_user("Ada", "ada@example.com")
        conversation = repository.create_conversation(user["user_id"], "Agent")
        run = repository.create_agent_run(user["user_id"], conversation["conversation_id"], goal="Search")
        graph = CareerAgentGraph(repository, ContextManager(repository, SkillRegistry()), model_factory=lambda _: None)
        calls = [
            {"name": "search_people", "args": {"request": {"person_type": "professor"}}, "id": "call_denied", "type": "tool_call"},
            {"name": "search_jobs", "args": {"request": {}}, "id": "call_budget", "type": "tool_call"},
        ]
        result = graph.execute_tools(
            {
                "user_id": user["user_id"],
                "conversation_id": conversation["conversation_id"],
                "run_id": run["run_id"],
                "intent": CareerIntent.JOB_SEARCH,
                "messages": [AIMessage(content="", tool_calls=calls)],
                "total_source_calls": 12,
                "iteration": 1,
                "job_candidates": [],
                "people_candidates": [],
            }
        )
        messages = result["messages"]
        self.assertEqual({item.tool_call_id for item in messages}, {"call_denied", "call_budget"})
        self.assertTrue(all(isinstance(item, ToolMessage) for item in messages))
        self.assertEqual(json.loads(messages[0].content)["error_type"], "ToolNotAuthorized")
        self.assertEqual(json.loads(messages[1].content)["error_type"], "SourceBudgetExhausted")
        engine.dispose()

    def test_candidate_merge_accumulates_and_merges_evidence(self):
        merged = CareerAgentGraph._merge_candidates(
            [{"candidate_id": "job_1", "evidence_ids": ["ev_1"]}],
            [{"candidate_id": "job_1", "title": "Updated", "evidence_ids": ["ev_2"]}, {"candidate_id": "job_2"}],
            2,
        )
        self.assertEqual([item["candidate_id"] for item in merged], ["job_1", "job_2"])
        self.assertEqual(merged[0]["evidence_ids"], ["ev_1", "ev_2"])


class _FailingClassifier:
    def __init__(self):
        self.calls = 0

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        self.calls += 1
        raise RuntimeError("Authorization: secret-token")


class _NeedsInputClassifier:
    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return IntentDecision(
            intent=CareerIntent.CLARIFICATION,
            goal="Clarify",
            needs_user_input=True,
            clarification_question="Which workflow should I use?",
        )


if __name__ == "__main__":
    unittest.main()
