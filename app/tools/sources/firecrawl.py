import os

from app.tools.sources.base import SourceResult


class FirecrawlAdapter:
    name = "firecrawl"

    def search(self, **_) -> SourceResult:
        if os.getenv("FIRECRAWL_ENABLED", "false").casefold() not in {"1", "true", "yes"}:
            return SourceResult(False, self.name, skipped=True, error_type="ProviderDisabled", error_message="Firecrawl is disabled.")
        if not os.getenv("FIRECRAWL_API_KEY"):
            return SourceResult(False, self.name, skipped=True, error_type="MissingCredential", error_message="FIRECRAWL_API_KEY is not configured.")
        return SourceResult(False, self.name, skipped=True, error_type="NotConfigured", error_message="Firecrawl provider is reserved but not required by the base workflow.")
