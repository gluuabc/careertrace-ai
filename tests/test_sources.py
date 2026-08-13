import json
import unittest
from unittest.mock import patch

from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.lever import LeverAdapter
from app.tools.sources.openalex import OpenAlexAdapter
from app.tools.sources.public_pages import PublicPageAdapter
from app.tools.sources.tavily import TavilyAdapter
from app.tools.sources.wikidata import WikidataAdapter
from app.tools.sources.playwright import PlaywrightAdapter


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


class FakeRaw:
    def __init__(self, content): self.content = content
    def read(self, size, decode_content=True): return self.content[:size]


class FakeResponse:
    def __init__(self, payload=None, content=b"", url="https://example.com", status=200, headers=None):
        self._payload = payload
        self.raw = FakeRaw(content)
        self.url = url
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = headers or {}
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("HTTP error")
    def json(self): return self._payload


class FakeSession:
    def __init__(self, response): self.response = response
    def get(self, *args, **kwargs): return self.response


class SequenceSession:
    def __init__(self, responses): self.responses = list(responses); self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


class FakePostSession:
    def __init__(self, response):
        self.response = response
        self.last_payload = None
    def post(self, *args, **kwargs):
        self.last_payload = kwargs.get("json")
        return self.response


class SourceAdapterTests(unittest.TestCase):
    def test_greenhouse_normalizes_without_inventing_fields(self):
        payload = {"jobs": [{"id": 1, "title": "Engineer Intern", "location": {"name": "NY"}, "absolute_url": "https://official/jobs/1", "content": "<p>Students graduating 2028</p>"}]}
        result = GreenhouseAdapter(FakeSession(FakeResponse(payload, url="https://boards-api.greenhouse.io/test"))).search(board_token="test", company="Example")
        self.assertTrue(result.ok)
        self.assertEqual(result.records[0]["company"], "Example")
        self.assertNotIn("salary", result.records[0])

    def test_lever_normalizes_public_posting(self):
        payload = [{"id": "x", "text": "Analyst", "categories": {"location": "Remote", "commitment": "Internship"}, "hostedUrl": "https://jobs.lever.co/x", "descriptionPlain": "Currently enrolled students"}]
        result = LeverAdapter(FakeSession(FakeResponse(payload))).search(site_name="example", company="Example")
        self.assertEqual(result.records[0]["employment_type"], "Internship")

    def test_public_page_rejects_non_https_and_strips_scripts(self):
        adapter = PublicPageAdapter(FakeSession(FakeResponse()), resolver=public_resolver)
        rejected = adapter.search(url="http://example.com", company="Example")
        self.assertFalse(rejected.ok)
        html = b"<script>bad()</script><a href='/jobs/1'>Software Engineer Job</a>"
        accepted = PublicPageAdapter(FakeSession(FakeResponse(content=html, url="https://example.com/careers")), resolver=public_resolver).search(url="https://example.com/careers", company="Example")
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.records[0]["application_url"], "https://example.com/jobs/1")

    def test_public_page_blocks_private_dns_destination(self):
        private = lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))]
        result = PublicPageAdapter(FakeSession(FakeResponse()), resolver=private).search(url="https://example.com/jobs")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ValueError")

    def test_public_page_revalidates_redirect_before_following(self):
        session = SequenceSession([FakeResponse(status=302, headers={"Location": "https://localhost/private"})])
        result = PublicPageAdapter(session, resolver=public_resolver).search(url="https://example.com/jobs")
        self.assertFalse(result.ok)
        self.assertEqual(session.calls, 1)

    def test_public_page_enforces_response_cap(self):
        oversized = b"x" * (PublicPageAdapter.max_bytes + 1)
        result = PublicPageAdapter(FakeSession(FakeResponse(content=oversized)), resolver=public_resolver).search(url="https://example.com/jobs")
        self.assertFalse(result.ok)
        self.assertIn("size", result.error_message)

    def test_network_adapters_enforce_response_caps(self):
        adapters = [
            (GreenhouseAdapter, FakeSession, lambda item: item.search(board_token="x",company="X")),
            (LeverAdapter, FakeSession, lambda item: item.search(site_name="x",company="X")),
            (OpenAlexAdapter, FakeSession, lambda item: item.search(query="x")),
            (WikidataAdapter, FakeSession, lambda item: item.search(query="x")),
            (TavilyAdapter, FakePostSession, lambda item: item.search(query="x")),
        ]
        for adapter_type, session_type, invoke in adapters:
            response=FakeResponse(content=b"x"*(adapter_type.max_bytes+1))
            adapter=adapter_type(session_type(response))
            environment={"TAVILY_ENABLED":"true","TAVILY_API_KEY":"test-only"} if adapter_type is TavilyAdapter else {}
            with self.subTest(adapter=adapter_type.__name__), patch.dict("os.environ",environment):
                result=invoke(adapter)
                self.assertFalse(result.ok)
                self.assertNotIn("test-only",repr(result))

    def test_public_job_detail_uses_only_explicit_structured_fields(self):
        html = b'''<script type="application/ld+json">{"@type":"JobPosting","title":"AI Intern","hiringOrganization":{"name":"Example"},"employmentType":"INTERN","datePosted":"2026-08-01","validThrough":"2026-09-01","description":"Undergraduate candidates must be currently enrolled. Sponsorship is not available."}</script><h1>Ignored fallback</h1>'''
        result = PublicPageAdapter(FakeSession(FakeResponse(content=html, url="https://example.com/jobs/1")), resolver=public_resolver).fetch_job_detail(url="https://example.com/jobs/1")
        record = result.records[0]
        self.assertEqual(record["title"], "AI Intern")
        self.assertEqual(record["company"], "Example")
        self.assertIn("Undergraduate", record["student_level"])
        self.assertIn("Sponsorship", record["work_authorization"])
        self.assertIsNone(record["salary"])

    def test_public_person_detail_requires_explicit_person_schema(self):
        html = b'''<script type="application/ld+json">{"@type":"Person","name":"Ada Example","jobTitle":"University Recruiter","worksFor":{"name":"Example Co"},"alumniOf":{"name":"Example University"},"email":"ada@example.com"}</script>'''
        result = PublicPageAdapter(FakeSession(FakeResponse(content=html, url="https://example.com/team/ada")), resolver=public_resolver).fetch_person_detail(url="https://example.com/team/ada")
        self.assertEqual(result.records[0]["name"], "Ada Example")
        self.assertEqual(result.records[0]["organization"], "Example Co")
        self.assertEqual(result.records[0]["public_contact"], "ada@example.com")

    def test_people_html_fallback_without_schema_org_requires_trust(self):
        html=b'''<h1>Ada Faculty</h1><div class="title">Professor of Computer Science</div><div class="institution">Example University</div><p>Research interests: machine learning, databases.</p><a href="mailto:ada@example.edu">Email</a>'''
        adapter=PublicPageAdapter(FakeSession(FakeResponse(content=html,url="https://example.edu/faculty/ada")),resolver=public_resolver)
        untrusted=adapter.fetch_person_detail(url="https://example.edu/faculty/ada")
        self.assertEqual(untrusted.records,[])
        trusted=adapter.fetch_person_detail(url="https://example.edu/faculty/ada",trusted_for_claims=True)
        self.assertEqual(trusted.records[0]["name"],"Ada Faculty")
        self.assertEqual(trusted.records[0]["organization"],"Example University")
        self.assertIn("machine learning",trusted.records[0]["research_topics"])

    def test_tavily_is_cleanly_skipped_when_disabled(self):
        with patch.dict("os.environ", {"TAVILY_ENABLED": "false", "TAVILY_API_KEY": ""}):
            result = TavilyAdapter(FakePostSession(FakeResponse())).search(query="internships")
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)

    def test_tavily_adapter_returns_discovery_records_without_key_leak(self):
        session = FakePostSession(FakeResponse({"results": [{"title": "Role", "url": "https://example.com/job", "content": "snippet", "score": 0.8}]}))
        with patch.dict("os.environ", {"TAVILY_ENABLED": "true", "TAVILY_API_KEY": "test-secret"}):
            result = TavilyAdapter(session).search(query="internships", max_results=2, include_domains=["example.com"])
        self.assertTrue(result.ok)
        self.assertTrue(result.records[0]["discovery_only"])
        self.assertNotIn("test-secret", repr(result))
        self.assertEqual(session.last_payload["include_domains"], ["example.com"])

    def test_playwright_is_disabled_by_default_and_blocks_private_destinations(self):
        with patch.dict("os.environ", {"PLAYWRIGHT_ENABLED": "false"}):
            skipped = PlaywrightAdapter(resolver=public_resolver).fetch(url="https://example.com/jobs")
        self.assertTrue(skipped.skipped)
        private = lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.0.0.2", 443))]
        with patch.dict("os.environ", {"PLAYWRIGHT_ENABLED": "true"}):
            blocked = PlaywrightAdapter(resolver=private).fetch(url="https://example.com/jobs")
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.source_status, "unavailable")


if __name__ == "__main__":
    unittest.main()
