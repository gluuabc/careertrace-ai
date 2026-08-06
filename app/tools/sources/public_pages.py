from __future__ import annotations

from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.tools.sources.base import SourceResult


class PublicPageAdapter:
    name = "official_public_page"
    timeout = (5.0, 15.0)
    max_bytes = 2_000_000
    user_agent = "CareerTrace/1.0 public-career-research"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.max_redirects = 5

    def search(self, *, url: str, company: str | None = None, **_) -> SourceResult:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            return SourceResult(False, self.name, source_url=url, error_type="UnsafeURL", error_message="Only absolute HTTPS public pages are allowed.")
        try:
            response = self.session.get(url, timeout=self.timeout, headers={"User-Agent": self.user_agent}, allow_redirects=True, stream=True)
            response.raise_for_status()
            raw = response.raw.read(self.max_bytes + 1, decode_content=True)
            if len(raw) > self.max_bytes:
                raise ValueError("Public page exceeded the response-size limit.")
            html = raw.decode(response.encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for item in soup(["script", "style", "iframe", "noscript"]):
                item.decompose()
            records = []
            for link in soup.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                href = urljoin(response.url, link["href"])
                if label and any(word in label.casefold() for word in ("intern", "engineer", "developer", "analyst", "research", "job")):
                    records.append({"title": label[:300], "company": company, "application_url": href, "description": None})
            return SourceResult(True, self.name, records[:100], html, response.url, "text/html")
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message=str(error)[:500], retryable=isinstance(error, requests.Timeout))
