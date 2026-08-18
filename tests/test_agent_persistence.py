import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.people_search import validate_connection_csv
from app.services.people_search import PeopleSearchService
from app.services.evidence import EvidenceService
from app.state.agent_schema import PeopleSearchRequest
from app.tools.sources.base import SourceResult
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

    def test_example_alumni_csv_role_alias_is_importable_and_searchable(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "demo"
            / "Example_Alumni_Connections.csv"
        )
        rows, errors = validate_connection_csv(fixture.read_text(encoding="utf-8"))

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(item["current_role"] for item in rows))
        self.assertTrue(all(item["education"] for item in rows))
        self.assertTrue(all(item["public_profile_url"] for item in rows))
        for row in rows:
            self.repository.create_connection(self.user["user_id"], row)
        self.assertEqual(len(self.repository.list_connections(self.user["user_id"])), 3)
        self.assertEqual(self.repository.list_connections(self.other["user_id"]), [])

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

    def test_search_session_resumes_and_reserves_budget_before_calls(self):
        run = self.repository.create_agent_run(
            self.user["user_id"], self.conversation["conversation_id"], goal="Search"
        )
        request = {"target_roles": ["Engineer"], "requested_count": 3}
        search = self.repository.get_or_create_search_session(
            self.user["user_id"],
            run["run_id"],
            intent="job_search",
            normalized_request=request,
            requested_count=3,
            source_call_budget=2,
        )
        resumed = self.repository.get_or_create_search_session(
            self.user["user_id"],
            run["run_id"],
            intent="job_search",
            normalized_request=request,
            requested_count=3,
            source_call_budget=2,
        )
        self.assertEqual(search["search_session_id"], resumed["search_session_id"])
        first = self.repository.reserve_search_source_calls(
            self.user["user_id"], search["search_session_id"], 2
        )
        second = self.repository.reserve_search_source_calls(
            self.user["user_id"], search["search_session_id"], 1
        )
        self.assertEqual(first["reserved_calls"], 2)
        self.assertEqual(second["reserved_calls"], 0)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.repository.reserve_search_source_calls(
                self.other["user_id"], search["search_session_id"], 1
            )

    def test_provider_network_call_not_inside_search_budget_transaction(self):
        run = self.repository.create_agent_run(
            self.user["user_id"], self.conversation["conversation_id"], goal="Search"
        )
        search = self.repository.get_or_create_search_session(
            self.user["user_id"], run["run_id"], intent="people_search",
            normalized_request={"person_type": "professor"}, requested_count=1,
            source_call_budget=1,
        )
        provider_called = False

        def provider_call():
            nonlocal provider_called
            provider_called = True

        reserved = self.repository.reserve_search_source_calls(
            self.user["user_id"], search["search_session_id"], 1
        )
        self.assertFalse(provider_called)
        self.assertEqual(reserved["reserved_calls"], 1)
        provider_call()
        self.assertTrue(provider_called)

    def test_private_connection_email_is_not_exposed_by_people_search(self):
        self.repository.create_connection(
            self.user["user_id"],
            {"name": "Grace", "education": "Example University", "public_profile_url": "https://example.com/grace", "user_provided_email": "private@example.com", "source_type": "manual"},
        )
        run = self.repository.create_agent_run(self.user["user_id"], self.conversation["conversation_id"], goal="Find alumni")

        class EmptySource:
            def search(self, **_kwargs):
                return SourceResult(True, "wikidata", [], "{}", "https://www.wikidata.org")

        service = PeopleSearchService(repository=self.repository, evidence=EvidenceService(self.repository), openalex=EmptySource(), wikidata=EmptySource())
        with patch.dict("os.environ", {"EVIDENCE_S3_ENABLED": "false"}):
            result = service.search(user_id=self.user["user_id"], run_id=run["run_id"], request=PeopleSearchRequest(person_type="alumni", school="Example University"), source_call_budget=2)
        serialized = str(result.model_dump(mode="json"))
        self.assertNotIn("private@example.com", serialized)
        candidate = result.data["page"]["items"][0]
        self.assertTrue(candidate["private_contact_reference"])
        self.assertEqual(candidate["contact_channels"], [])


if __name__ == "__main__":
    unittest.main()
