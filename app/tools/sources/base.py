from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


def bounded_response_bytes(response: Any, max_bytes: int) -> bytes:
    raw = getattr(response, "raw", None)
    if raw is not None and hasattr(raw, "read"):
        content = raw.read(max_bytes + 1, decode_content=True)
    else:
        content = bytes(getattr(response, "content", b""))
    if len(content) > max_bytes:
        raise ValueError("Provider response exceeded the configured size limit.")
    return content


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
    cursor: str | None = None
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int | None = None
    total_count_is_estimate: bool = True
    source_status: str | None = None


class SourceAdapter(Protocol):
    name: str
    timeout: tuple[float, float]

    def search(self, **kwargs: Any) -> SourceResult: ...
