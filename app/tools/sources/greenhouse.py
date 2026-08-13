from __future__ import annotations

import json
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.tools.sources.base import SourceResult, bounded_response_bytes


class GreenhouseAdapter:
    name = "greenhouse"
    timeout = (5.0, 15.0)
    user_agent = "CareerTrace/1.0 public-job-research"
    max_bytes = 5_000_000

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def search(self, *, board_token: str, company: str, **_: Any) -> SourceResult:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                allow_redirects=False,
                stream=True,
            )
            response.raise_for_status()
            raw = bounded_response_bytes(response, self.max_bytes)
            if not raw and hasattr(response, "json"):
                raw = json.dumps(response.json()).encode("utf-8")
            payload = json.loads(raw)
            records = []
            for item in payload.get("jobs") or []:
                description = BeautifulSoup(item.get("content") or "", "html.parser").get_text(" ", strip=True)
                records.append(
                    {
                        "source_job_id": str(item.get("id")) if item.get("id") is not None else None,
                        "title": item.get("title"),
                        "company": company,
                        "location": (item.get("location") or {}).get("name"),
                        "application_url": item.get("absolute_url"),
                        "posted_at": item.get("updated_at"),
                        "description": description,
                    }
                )
            return SourceResult(True, self.name, records, raw.decode("utf-8", errors="replace"), url)
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message="Greenhouse source request failed.", retryable=isinstance(error, requests.Timeout))
