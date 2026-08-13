from __future__ import annotations

from collections.abc import Callable
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.tools.sources.base import SourceResult
from app.tools.sources.url_safety import validate_public_https_url


class PublicPageAdapter:
    name = "official_public_page"
    timeout = (5.0, 15.0)
    max_bytes = 2_000_000
    max_redirects = 5
    user_agent = "CareerTrace/1.0 public-career-research"

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        resolver: Callable | None = None,
    ):
        self.session = session or requests.Session()
        self.resolver = resolver

    def _validate(self, url: str, allowed_hosts: set[str] | None = None) -> str:
        kwargs = {"allowed_hosts": allowed_hosts}
        if self.resolver is not None:
            kwargs["resolver"] = self.resolver
        return validate_public_https_url(url, **kwargs)

    def _fetch(self, url: str, *, allowed_hosts: set[str] | None = None):
        current = url
        for _ in range(self.max_redirects + 1):
            self._validate(current, allowed_hosts)
            response = self.session.get(
                current,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = getattr(response, "headers", {}).get("Location")
                if not location:
                    raise ValueError("Redirect response omitted its destination.")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            raw = response.raw.read(self.max_bytes + 1, decode_content=True)
            if len(raw) > self.max_bytes:
                raise ValueError("Public page exceeded the response-size limit.")
            return response, raw
        raise ValueError("Public page exceeded the redirect limit.")

    @staticmethod
    def _clean_soup(raw: bytes, encoding: str | None) -> tuple[BeautifulSoup, str]:
        html = raw.decode(encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for item in soup(["script", "style", "iframe", "noscript"]):
            item.decompose()
        return soup, html

    def discover_listings(
        self,
        *,
        url: str,
        company: str | None = None,
        allowed_hosts: set[str] | None = None,
        **_,
    ) -> SourceResult:
        try:
            response, raw = self._fetch(url, allowed_hosts=allowed_hosts)
            soup, html = self._clean_soup(raw, getattr(response, "encoding", None))
            records = []
            for link in soup.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                href = urljoin(response.url, link["href"])
                if label and any(word in label.casefold() for word in ("intern", "engineer", "developer", "analyst", "research", "job")):
                    records.append(
                        {
                            "title": label[:300],
                            "company": company,
                            "application_url": href,
                            "short_snippet": None,
                            "source_metadata": {"discovered_from": response.url},
                        }
                    )
            return SourceResult(
                True,
                self.name,
                records[:100],
                html,
                response.url,
                "text/html",
                total_count=len(records),
                total_count_is_estimate=False,
                source_status="available",
            )
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message=str(error)[:500], retryable=isinstance(error, requests.Timeout), source_status="unavailable")

    def fetch_job_detail(
        self,
        *,
        url: str,
        company: str | None = None,
        allowed_hosts: set[str] | None = None,
    ) -> SourceResult:
        try:
            response, raw = self._fetch(url, allowed_hosts=allowed_hosts)
            raw_html = raw.decode(getattr(response, "encoding", None) or "utf-8", errors="replace")
            structured_soup = BeautifulSoup(raw_html, "html.parser")
            posting = {}
            for script in structured_soup.find_all("script", attrs={"type": "application/ld+json"}):
                try:
                    value = json.loads(script.string or script.get_text() or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                candidates = value if isinstance(value, list) else (value.get("@graph", []) if isinstance(value, dict) and "@graph" in value else [value])
                posting = next((item for item in candidates if isinstance(item, dict) and "JobPosting" in str(item.get("@type"))), posting)
            soup, html = self._clean_soup(raw, getattr(response, "encoding", None))
            text = " ".join(soup.get_text(" ", strip=True).split())
            title_node = soup.find("h1") or soup.find("title")
            title = posting.get("title") or (title_node.get_text(" ", strip=True)[:300] if title_node else None)
            organization = posting.get("hiringOrganization") or {}
            location_value = posting.get("jobLocation")
            if isinstance(location_value, list):
                location_value = location_value[0] if location_value else None
            address = location_value.get("address") if isinstance(location_value, dict) else None
            if isinstance(address, dict):
                location = ", ".join(str(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)) or None
            else:
                location = str(address).strip() if address else None
            structured_description = posting.get("description")
            if structured_description:
                structured_description = BeautifulSoup(str(structured_description), "html.parser").get_text(" ", strip=True)
            description = structured_description or text or None
            def explicit_sentence(pattern: str) -> str | None:
                match = re.search(rf"([^.!?]*{pattern}[^.!?]*[.!?]?)", description or "", re.I)
                return " ".join(match.group(1).split())[:1000] if match else None
            eligibility_parts = [posting.get("qualifications"), posting.get("educationRequirements"), posting.get("experienceRequirements")]
            eligibility = " ".join(str(item) for item in eligibility_parts if item).strip() or explicit_sentence(r"currently enrolled|graduat(?:e|ing|ion)|work authorization|sponsorship")
            salary_value = posting.get("baseSalary")
            record = {
                "title": title,
                "company": organization.get("name") if isinstance(organization, dict) and organization.get("name") else company,
                "location": location,
                "employment_type": posting.get("employmentType"),
                "description": description,
                "eligibility": eligibility,
                "student_level": explicit_sentence(r"undergraduate|graduate student|high school|bachelor|master|doctoral|phd"),
                "graduation_requirement": explicit_sentence(r"graduat(?:e|ing|ion)"),
                "work_authorization": explicit_sentence(r"work authorization|authorized to work|sponsorship|citizen"),
                "salary": json.dumps(salary_value, ensure_ascii=False) if isinstance(salary_value, (dict, list)) else salary_value,
                "posted_at": posting.get("datePosted"),
                "deadline": posting.get("validThrough"),
                "application_url": response.url,
            }
            return SourceResult(True, self.name, [record], html, response.url, "text/html", total_count=1, total_count_is_estimate=False, source_status="available")
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message=str(error)[:500], retryable=isinstance(error, requests.Timeout), source_status="unavailable")

    def fetch_person_detail(
        self,
        *,
        url: str,
        allowed_hosts: set[str] | None = None,
        trusted_for_claims: bool = False,
    ) -> SourceResult:
        """Extract explicit Person data, with a bounded trusted-page HTML fallback."""
        try:
            response, raw = self._fetch(url, allowed_hosts=allowed_hosts)
            html = raw.decode(getattr(response, "encoding", None) or "utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            people = []
            for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
                try:
                    value = json.loads(script.string or script.get_text() or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                values = value if isinstance(value, list) else (value.get("@graph", []) if isinstance(value, dict) and "@graph" in value else [value])
                for item in values:
                    if not isinstance(item, dict) or "Person" not in str(item.get("@type")) or not item.get("name"):
                        continue
                    organization = item.get("worksFor") or item.get("affiliation")
                    if isinstance(organization, list):
                        organization = organization[0] if organization else None
                    organization_name = organization.get("name") if isinstance(organization, dict) else organization
                    alumni = item.get("alumniOf")
                    if isinstance(alumni, list):
                        alumni = [entry.get("name") if isinstance(entry, dict) else entry for entry in alumni]
                    elif isinstance(alumni, dict):
                        alumni = alumni.get("name")
                    topics = item.get("knowsAbout") or []
                    if isinstance(topics, str):
                        topics = [topics]
                    people.append({
                        "name": str(item["name"]),
                        "current_role": item.get("jobTitle"),
                        "organization": organization_name,
                        "education": alumni,
                        "research_topics": topics,
                        "public_source_url": response.url,
                        "public_profiles": [item.get("url")] if item.get("url") else [response.url],
                        "public_contact": item.get("email"),
                        "claim_provenance": {"method": "schema_org_person", "source_url": response.url},
                    })
            if not people and trusted_for_claims:
                visible, _ = self._clean_soup(raw, getattr(response, "encoding", None))
                name_node = visible.find("h1")
                name = name_node.get_text(" ", strip=True)[:300] if name_node else None
                role_node = visible.select_one("[class*='title'], [class*='role'], [class*='position']")
                organization_node = visible.select_one("[class*='affiliation'], [class*='institution'], [class*='department'], [class*='organization']")
                role = role_node.get_text(" ", strip=True)[:300] if role_node else None
                organization = organization_node.get_text(" ", strip=True)[:300] if organization_node else None
                text = " ".join(visible.get_text(" ", strip=True).split())
                if name and (role or organization):
                    topics: list[str] = []
                    topic_match = re.search(r"(?:research interests?|research areas?|topics?)\s*:?\s*([^.!?]{3,500})", text, re.I)
                    if topic_match:
                        topics = [item.strip() for item in re.split(r"[,;]", topic_match.group(1)) if item.strip()][:10]
                    public_email = None
                    mail = visible.find("a", href=re.compile(r"^mailto:", re.I))
                    if mail:
                        public_email = str(mail.get("href") or "").removeprefix("mailto:").split("?", 1)[0]
                    people.append({
                        "name": name,
                        "current_role": role,
                        "organization": organization,
                        "education": None,
                        "research_topics": topics,
                        "public_source_url": response.url,
                        "public_profiles": [response.url],
                        "public_contact": public_email,
                        "claim_provenance": {"method": "visible_html", "source_url": response.url, "explicit_text_excerpt": text[:1000]},
                    })
            return SourceResult(True, self.name, people, html, response.url, "text/html", total_count=len(people), total_count_is_estimate=False, source_status="available")
        except (requests.RequestException, ValueError) as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message="Public person detail is unavailable.", retryable=isinstance(error, requests.Timeout), source_status="unavailable")

    # Backward-compatible entry point used by the current catalog service.
    def search(self, **kwargs) -> SourceResult:
        return self.discover_listings(**kwargs)
