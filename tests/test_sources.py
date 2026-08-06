import json
import unittest

from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.lever import LeverAdapter
from app.tools.sources.public_pages import PublicPageAdapter


class FakeRaw:
    def __init__(self, content): self.content = content
    def read(self, size, decode_content=True): return self.content[:size]


class FakeResponse:
    def __init__(self, payload=None, content=b"", url="https://example.com", status=200):
        self._payload = payload
        self.raw = FakeRaw(content)
        self.url = url
        self.status_code = status
        self.encoding = "utf-8"
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("HTTP error")
    def json(self): return self._payload


class FakeSession:
    def __init__(self, response): self.response = response
    def get(self, *args, **kwargs): return self.response


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
        rejected = PublicPageAdapter(FakeSession(FakeResponse())).search(url="http://example.com", company="Example")
        self.assertFalse(rejected.ok)
        html = b"<script>bad()</script><a href='/jobs/1'>Software Engineer Job</a>"
        accepted = PublicPageAdapter(FakeSession(FakeResponse(content=html, url="https://example.com/careers"))).search(url="https://example.com/careers", company="Example")
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.records[0]["application_url"], "https://example.com/jobs/1")


if __name__ == "__main__":
    unittest.main()
