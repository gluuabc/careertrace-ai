from __future__ import annotations

import re


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|secret|password|token|credential)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|secret)=)[^&#\s]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/@\s]+@")


def sanitize_diagnostic(error: object, *, limit: int = 1000) -> str:
    """Return bounded diagnostics that are safe to persist in trajectories."""

    value = str(error).replace("\x00", "")
    value = _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    value = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", value)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    return value[:limit]


def safe_provider_message(action: str) -> str:
    return (
        f"CareerTrace could not complete {action}. No unsafe partial action was "
        "taken. Please retry once or provide a more specific target."
    )
