import unittest
from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.evidence import EvidenceService
from app.services.job_search import JobSearchService, apply_hard_filters, deduplicate_jobs, extract_explicit_eligibility
from app.state.agent_schema import JobCandidate, JobSearchRequest
from app.tools.sources.base import SourceResult
from app.tools.sources.catalog import CompanySource


def candidate(**updates):
    data = {"candidate_id": "job_1", "title": "Software Engineer Intern", "company": "Example", "location": "New York", "source_name": "greenhouse", "source_url": "https://example.com/source", "application_url": "https://example.com/jobs/1?ref=x"}
    data.update(updates)
    return JobCandidate(**data)


class JobSearchTests(unittest.TestCase):
    def test_unknown_eligibility_does_not_pass(self):
        item = apply_hard_filters(candidate(), JobSearchRequest(target_roles=["Engineer"]))
        self.assertFalse(item.hard_constraints_met)
        self.assertIn("eligibility_unknown", item.failed_hard_constraints)

    def test_explicit_eligibility_and_hard_constraints(self):
        eligibility = extract_explicit_eligibility("Candidates must be currently enrolled students graduating in 2028.")
        item = apply_hard_filters(candidate(eligibility=eligibility), JobSearchRequest(target_roles=["Engineer"], graduation_year=2028, locations=["New York"]))
        self.assertTrue(item.hard_constraints_met)

    def test_deduplicates_canonical_url(self):
        records = [{"title": "Engineer", "company": "X", "location": "NY", "application_url": "https://example.com/job/1?x=1"}, {"title": "Engineer", "company": "X", "location": "NY", "application_url": "https://example.com/job/1?x=2"}]
        self.assertEqual(len(deduplicate_jobs(records)), 1)

    def test_student_level_must_be_explicit_or_remains_unknown(self):
        item = apply_hard_filters(
            candidate(eligibility="Candidates must be currently enrolled students."),
            JobSearchRequest(student_level="undergraduate"),
        )
        self.assertFalse(item.hard_constraints_met)
        self.assertIn("student_level_unknown", item.failed_hard_constraints)
        verified = apply_hard_filters(
            candidate(eligibility="Candidates must be currently enrolled undergraduate students."),
            JobSearchRequest(student_level="undergraduate"),
        )
        self.assertTrue(verified.hard_constraints_met)

    def test_search_page_is_bounded_and_cached_for_internal_pagination(self):
        engine = create_database_engine("sqlite://")
        init_db(engine)
        repository = ProfileRepository(create_session_factory(engine))
        user = repository.get_or_create_user("Ada", "ada-search@example.com")
        conversation = repository.create_conversation(user["user_id"], "Search")
        run = repository.create_agent_run(user["user_id"], conversation["conversation_id"], goal="Search")

        class Catalog:
            source = CompanySource(company="Example", ats_type="greenhouse", board_token="example", enabled=True, verification_status="verified")
            def enabled(self): return [self.source]
            def find(self, _name): return self.source

        class Adapter:
            calls = 0
            def search(self, **_kwargs):
                self.calls += 1
                records = [{"source_job_id": str(index), "title": f"Engineer Intern {index}", "company": "Example", "location": "Remote", "application_url": f"https://example.com/jobs/{index}", "description": "Currently enrolled student. " + "x" * 600} for index in range(12)]
                return SourceResult(True, "greenhouse", records, "raw feed", "https://example.com/feed")

        adapter = Adapter()
        service = JobSearchService(catalog=Catalog(), greenhouse=adapter, repository=repository, evidence=EvidenceService(repository))
        with patch.dict("os.environ", {"EVIDENCE_S3_ENABLED": "false"}):
            first = service.search(user_id=user["user_id"], run_id=run["run_id"], request=JobSearchRequest(target_roles=["Engineer"], profile_skills=["Python"], desired_job_skills=["SQL"], requested_count=12, max_results=20, page_size=10), source_call_budget=2)
            second = service.search(user_id=user["user_id"], run_id=run["run_id"], request=JobSearchRequest(target_roles=["Engineer"], profile_skills=["Python"], desired_job_skills=["SQL"], requested_count=12, max_results=20, page_size=10, cursor="10"), source_call_budget=2)
        self.assertEqual(first.data["page"]["returned_count"], 10)
        self.assertEqual(first.data["page"]["next_cursor"], "10")
        self.assertTrue(all(len(item["description_excerpt"]) <= 300 for item in first.data["page"]["items"]))
        self.assertEqual(second.data["page"]["returned_count"], 2)
        self.assertEqual(adapter.calls, 1)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
