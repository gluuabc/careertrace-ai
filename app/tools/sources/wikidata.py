from __future__ import annotations

import json

import requests

from app.tools.sources.base import SourceResult, bounded_response_bytes


class WikidataAdapter:
    name = "wikidata"
    timeout = (5.0, 15.0)
    max_bytes = 3_000_000

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def search(self, *, query: str, limit: int = 10, **_) -> SourceResult:
        url = "https://www.wikidata.org/w/api.php"
        params = {"action": "wbsearchentities", "search": query, "language": "en", "format": "json", "limit": min(limit, 20)}
        try:
            response = self.session.get(url, params=params, timeout=self.timeout, headers={"User-Agent": "CareerTrace/1.0 public-identity-research"}, stream=True, allow_redirects=False)
            response.raise_for_status()
            raw = bounded_response_bytes(response, self.max_bytes)
            payload = json.loads(raw) if raw else response.json()
            records = [{"name": item.get("label"), "description": item.get("description"), "public_source_url": item.get("concepturi"), "public_profiles": [item.get("concepturi")] if item.get("concepturi") else []} for item in payload.get("search") or []]
            return SourceResult(True, self.name, records, raw.decode("utf-8", errors="replace") if raw else json.dumps(payload), response.url)
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message="Wikidata request failed.", retryable=isinstance(error, requests.Timeout))
