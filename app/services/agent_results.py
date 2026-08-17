from __future__ import annotations

from typing import Any

from app.database.repository import ProfileRepository


def resolve_agent_display_result(
    repository: ProfileRepository,
    user_id: str,
    conversation_id: str,
    cached_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use a correctly scoped fresh result or rehydrate the active run from SQL."""

    cached = cached_result or {}
    if (
        cached.get("user_id") == user_id
        and cached.get("conversation_id") == conversation_id
    ):
        return cached
    return repository.get_latest_agent_display_result(user_id, conversation_id) or {}


def primary_job_link(candidate: dict[str, Any]) -> tuple[str, str] | None:
    """Prefer the human-facing posting while retaining provenance separately."""

    application_url = str(candidate.get("application_url") or "").strip()
    if application_url:
        return "View official posting", application_url
    source_url = str(candidate.get("source_url") or "").strip()
    if source_url:
        return "View source", source_url
    return None
