from __future__ import annotations

import os
import json
from typing import Any

import requests

from app.tools.sources.base import SourceResult, bounded_response_bytes


class TavilyAdapter:
    """Optional discovery-only adapter; returned snippets are never evidence."""

    name = "tavily_discovery"
    endpoint = "https://api.tavily.com/search"
    timeout = (5.0, 15.0)
    max_bytes = 2_000_000

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        include_domains: list[str] | None = None,
    ) -> SourceResult:
        enabled = os.getenv("TAVILY_ENABLED", "false").strip().casefold() == "true"
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not enabled or not api_key:
            return SourceResult(
                ok=True,
                source_name=self.name,
                skipped=True,
                source_status="skipped",
                error_type="ProviderDisabled",
                error_message="Optional Tavily discovery is not configured.",
            )
        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": query.strip(),
            "max_results": max(1, min(int(max_results), 10)),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains[:10]
        try:
            response = self.session.post(self.endpoint, json=payload, timeout=self.timeout, stream=True, allow_redirects=False)
            response.raise_for_status()
            raw = bounded_response_bytes(response, self.max_bytes)
            body = json.loads(raw) if raw else response.json()
            records = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "short_snippet": item.get("content"),
                    "ranking_metadata": {"score": item.get("score")},
                    "discovery_only": True,
                }
                for item in body.get("results", [])
                if item.get("url")
            ]
            return SourceResult(
                ok=True,
                source_name=self.name,
                records=records,
                source_url=self.endpoint,
                content_type="application/json",
                total_count=len(records),
                total_count_is_estimate=True,
                source_status="available",
            )
        except (requests.RequestException, ValueError, TypeError) as error:
            return SourceResult(
                ok=False,
                source_name=self.name,
                source_url=self.endpoint,
                error_type=type(error).__name__,
                error_message="Tavily discovery request failed.",
                retryable=isinstance(error, requests.Timeout),
                source_status="unavailable",
            )
