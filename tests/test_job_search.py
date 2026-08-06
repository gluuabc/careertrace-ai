import unittest

from app.services.job_search import apply_hard_filters, deduplicate_jobs, extract_explicit_eligibility
from app.state.agent_schema import JobCandidate, JobSearchRequest


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


if __name__ == "__main__":
    unittest.main()
