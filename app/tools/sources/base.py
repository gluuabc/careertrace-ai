from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SourceResult:
    ok: bool
    source_name: str
    records: list[dict[str, Any]] = field(default_factory=list)
    raw_content: str = ""
    source_url: str | None = None
    content_type: str = "application/json"
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    skipped: bool = False


class SourceAdapter(Protocol):
    name: str
    timeout: tuple[float, float]

    def search(self, **kwargs: Any) -> SourceResult: ...
