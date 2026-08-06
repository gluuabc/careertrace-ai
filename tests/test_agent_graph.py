import unittest

from app.graph.career_agent_graph import CareerAgentGraph, _fallback_intent
from app.state.agent_schema import CareerIntent
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


if __name__ == "__main__":
    unittest.main()
