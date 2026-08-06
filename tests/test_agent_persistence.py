import unittest

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.people_search import validate_connection_csv
from app.services.trajectory import sanitize_arguments


class AgentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        self.other = self.repository.get_or_create_user("Other", "other@example.com")
        self.conversation = self.repository.create_conversation(self.user["user_id"], "Agent")
        self.profile = self.repository.upsert_profile(self.user["user_id"], {"school": "Example", "major": "CS", "graduation_year": 2028, "skills": ["Python"], "experience": [{"role": "Intern"}]})

    def tearDown(self):
        self.engine.dispose()

    def test_run_trajectory_is_scoped_and_arguments_are_sanitized(self):
        run = self.repository.create_agent_run(self.user["user_id"], self.conversation["conversation_id"], goal="Search")
        step = self.repository.create_agent_step(self.user["user_id"], run["run_id"], stage="search", status="completed", display_summary="Searched one source")
        args = sanitize_arguments({"query": "intern", "api_key": "secret", "thinking": "private"})
        self.repository.record_agent_tool_call(self.user["user_id"], run["run_id"], tool_call_id="call-1", tool_name="search_jobs", sanitized_arguments=args, status="completed", step_id=step["step_id"])
        stored = self.repository.list_agent_runs(self.user["user_id"], self.conversation["conversation_id"])[0]
        self.assertEqual(stored["tool_calls"][0]["sanitized_arguments"]["api_key"], "[REDACTED]")
        self.assertNotIn("thinking", stored["tool_calls"][0]["sanitized_arguments"])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.repository.update_agent_run(self.other["user_id"], run["run_id"], status="failed")

    def test_resume_and_outreach_drafts_are_user_scoped_and_unsent(self):
        draft = self.repository.save_resume_revision_draft(self.user["user_id"], {"source_profile_version_id": self.profile["profile_version_id"], "summary": "Tailored summary", "changes": [{"section": "summary", "proposed_text": "Python engineer", "rationale": "Role alignment"}]})
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(self.repository.list_resume_revision_drafts(self.other["user_id"]), [])
        outreach = self.repository.save_outreach_draft(self.user["user_id"], {"outreach_type": "alumni_outreach", "recipient_name": "Grace", "subject": "Career question", "body": "Could I ask about your experience?"})
        self.assertEqual(outreach["status"], "draft")
        self.assertIsNone(outreach["sent_at"])
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.repository.update_outreach_status(self.user["user_id"], outreach["draft_id"], "sent", explicit_user_action=False)
        ready = self.repository.update_outreach_status(self.user["user_id"], outreach["draft_id"], "ready", explicit_user_action=True)
        sent = self.repository.update_outreach_status(self.user["user_id"], ready["draft_id"], "sent", explicit_user_action=True)
        self.assertIsNotNone(sent["sent_at"])

    def test_csv_formula_and_row_errors(self):
        rows, errors = validate_connection_csv("name,current_role\n=CMD(),Recruiter\nAda,Engineer")
        self.assertEqual([item["name"] for item in rows], ["Ada"])
        self.assertTrue(any("formula" in item for item in errors))

    def test_no_response_follow_up_requires_matching_sent_draft(self):
        with self.assertRaisesRegex(ValueError, "previous outreach"):
            self.repository.save_outreach_draft(
                self.user["user_id"],
                {
                    "outreach_type": "no_response_follow_up",
                    "recipient_name": "Grace",
                    "subject": "Following up",
                    "body": "Following up on my earlier note.",
                },
            )
        previous = self.repository.save_outreach_draft(
            self.user["user_id"],
            {
                "outreach_type": "alumni_outreach",
                "recipient_name": "Grace",
                "subject": "Career question",
                "body": "Could I ask about your experience?",
            },
        )
        ready = self.repository.update_outreach_status(
            self.user["user_id"], previous["draft_id"], "ready", explicit_user_action=True
        )
        sent = self.repository.update_outreach_status(
            self.user["user_id"], ready["draft_id"], "sent", explicit_user_action=True
        )
        follow_up = self.repository.save_outreach_draft(
            self.user["user_id"],
            {
                "outreach_type": "no_response_follow_up",
                "recipient_name": "Grace",
                "subject": "Following up",
                "body": "Following up on my earlier note.",
                "previous_draft_id": sent["draft_id"],
            },
        )
        self.assertEqual(follow_up["status"], "draft")


if __name__ == "__main__":
    unittest.main()
