import os

from app.tools.sources.base import SourceResult


class PlaywrightAdapter:
    name = "playwright"

    def search(self, **_) -> SourceResult:
        if os.getenv("PLAYWRIGHT_ENABLED", "false").casefold() not in {"1", "true", "yes"}:
            return SourceResult(False, self.name, skipped=True, error_type="ProviderDisabled", error_message="Playwright is disabled.")
        return SourceResult(False, self.name, skipped=True, error_type="ProviderUnavailable", error_message="No browser binaries are installed automatically.")
