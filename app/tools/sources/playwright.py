from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.tools.sources.base import SourceResult
from app.tools.sources.url_safety import validate_public_https_url


class PlaywrightAdapter:
    """JS fallback for one known URL; deliberately not a discovery/search engine."""

    name = "playwright"
    timeout_ms = 20_000
    max_bytes = 2_000_000

    def __init__(self, *, resolver: Callable | None = None, playwright_factory=None):
        self.resolver = resolver
        self.playwright_factory = playwright_factory

    def _validate(self, url: str, allowed_hosts: set[str]) -> None:
        kwargs = {"allowed_hosts": allowed_hosts}
        if self.resolver is not None:
            kwargs["resolver"] = self.resolver
        validate_public_https_url(url, **kwargs)

    def fetch(self, *, url: str, allowed_hosts: set[str] | None = None, company: str | None = None) -> SourceResult:
        if os.getenv("PLAYWRIGHT_ENABLED", "false").casefold() not in {"1", "true", "yes"}:
            return SourceResult(True, self.name, skipped=True, error_type="ProviderDisabled", error_message="Playwright is disabled.", source_status="skipped")
        host = (urlparse(url).hostname or "").casefold()
        hosts = allowed_hosts or ({host} if host else set())
        try:
            self._validate(url, hosts)
            if self.playwright_factory is None:
                from playwright.sync_api import sync_playwright
                factory = sync_playwright
            else:
                factory = self.playwright_factory
            with factory() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()

                def guard(route):
                    try:
                        self._validate(route.request.url, hosts)
                        route.continue_()
                    except ValueError:
                        route.abort()

                page.route("**/*", guard)
                response = page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
                final_url = page.url
                self._validate(final_url, hosts)
                if response is None or response.status >= 400:
                    raise ValueError("Rendered page did not return a usable response.")
                html = page.content()
                browser.close()
            if len(html.encode("utf-8")) > self.max_bytes:
                raise ValueError("Rendered page exceeded the response-size limit.")
            soup = BeautifulSoup(html, "html.parser")
            for item in soup(["script", "style", "iframe", "noscript"]):
                item.decompose()
            records = []
            for link in soup.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                if label and any(word in label.casefold() for word in ("intern", "engineer", "developer", "analyst", "research", "job")):
                    records.append({"title": label[:300], "company": company, "application_url": urljoin(final_url, link["href"]), "short_snippet": None, "source_metadata": {"rendered_from": final_url}})
            return SourceResult(True, self.name, records[:100], html, final_url, "text/html", total_count=len(records), total_count_is_estimate=False, source_status="available")
        except ImportError:
            return SourceResult(False, self.name, skipped=True, error_type="ProviderUnavailable", error_message="Playwright package or browser binaries are unavailable.", source_status="unavailable")
        except Exception as error:
            return SourceResult(False, self.name, source_url=url, error_type=type(error).__name__, error_message="Playwright could not render the validated public page.", source_status="unavailable")

    def search(self, **kwargs) -> SourceResult:
        return self.fetch(**kwargs)
