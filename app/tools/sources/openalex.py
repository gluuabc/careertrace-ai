from __future__ import annotations

import json
import os

import requests

from app.tools.sources.base import SourceResult, bounded_response_bytes


class OpenAlexAdapter:
    name = "openalex"
    timeout = (5.0, 15.0)
    max_bytes = 3_000_000

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def search(self, *, query: str, limit: int = 10, **_) -> SourceResult:
        url = "https://api.openalex.org/authors"
        params = {"search": query, "per-page": min(limit, 20)}
        if os.getenv("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        try:
            response = self.session.get(url, params=params, timeout=self.timeout, headers={"User-Agent": "CareerTrace/1.0 (public academic discovery)"}, stream=True, allow_redirects=False)
            response.raise_for_status()
            raw = bounded_response_bytes(response, self.max_bytes)
            payload = json.loads(raw) if raw else response.json()
            records = []
            for item in payload.get("results") or []:
                affiliation = item.get("last_known_institutions") or []
                records.append({"name": item.get("display_name"), "current_role": "Researcher", "organization": affiliation[0].get("display_name") if affiliation else None, "research_topics": [topic.get("display_name") for topic in (item.get("topics") or [])[:5]], "public_source_url": item.get("id"), "public_profiles": [item.get("orcid")] if item.get("orcid") else []})
            return SourceResult(True, self.name, records, raw.decode("utf-8", errors="replace") if raw else json.dumps(payload), response.url)
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message="OpenAlex request failed.", retryable=isinstance(error, requests.Timeout))
