from __future__ import annotations

import json
from typing import Any

import requests

from app.tools.sources.base import SourceResult


class LeverAdapter:
    name = "lever"
    timeout = (5.0, 15.0)
    user_agent = "CareerTrace/1.0 public-job-research"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def search(self, *, site_name: str, company: str, **_: Any) -> SourceResult:
        url = f"https://api.lever.co/v0/postings/{site_name}?mode=json"
        try:
            response = self.session.get(url, timeout=self.timeout, headers={"User-Agent": self.user_agent}, allow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            records = [
                {
                    "source_job_id": item.get("id"),
                    "title": item.get("text"),
                    "company": company,
                    "location": (item.get("categories") or {}).get("location"),
                    "employment_type": (item.get("categories") or {}).get("commitment"),
                    "application_url": item.get("hostedUrl") or item.get("applyUrl"),
                    "description": "\n".join(str(item.get(key) or "") for key in ("descriptionPlain", "additionalPlain")),
                }
                for item in payload
            ]
            return SourceResult(True, self.name, records, json.dumps(payload), url)
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message=str(error)[:500], retryable=isinstance(error, requests.Timeout))
