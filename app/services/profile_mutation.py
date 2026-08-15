from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.database import profile_repository
from app.database.repository import ProfileRepository
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval_corpus import RetrievalCorpusIndexer


def is_profile_field_change_stale(
    *, field_key: str, before_value: Any, current_profile: dict[str, Any]
) -> bool:
    """A proposed field change is stale only when that same field changed."""

    return current_profile.get(field_key) != before_value


class ProfileMutationService:
    """Canonical boundary for current profile mutation and retrieval refresh."""

    def __init__(
        self,
        repository: ProfileRepository = profile_repository,
        indexer: RetrievalCorpusIndexer | None = None,
    ):
        self.repository = repository
        self.indexer = indexer or RetrievalCorpusIndexer(
            RetrievalRepository(repository.session_factory)
        )

    def apply_profile_field_changes(
        self,
        user_id: str,
        field_changes: dict[str, Any],
        *,
        source_type: str,
        source_conversation_id: str | None = None,
        source_message_ids: Iterable[str] | None = None,
        document_ids: Iterable[str] | None = None,
        operations: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        profile = self.repository.apply_profile_field_changes(
            user_id,
            field_changes,
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            source_message_ids=source_message_ids,
            document_ids=document_ids,
            operations=operations,
        )
        if not profile["profile_changed"]:
            return profile
        return self.refresh_profile_retrieval(user_id, profile)

    def refresh_profile_retrieval(
        self, user_id: str, profile: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        current = profile or self.repository.get_profile(user_id)
        if current is None or not current.get("profile_version_id"):
            raise ValueError("A current profile version is required for indexing.")
        version_id = current["profile_version_id"]
        try:
            records = self.indexer.index_profile(user_id=user_id, profile=current)
            warnings = [
                item.get("metadata", {}).get("indexing_warning")
                for item in records
                if item.get("metadata", {}).get("indexing_warning")
            ]
            status = "sparse_only" if warnings else "ready"
            error = warnings[0] if warnings else None
        except Exception:
            status = "failed"
            error = "Profile retrieval indexing failed; retry is required."
        self.repository.set_profile_retrieval_index_status(
            user_id, version_id, status=status, error=error
        )
        current.update(
            retrieval_index_status=status,
            retrieval_index_error=error,
        )
        return current


profile_mutation_service = ProfileMutationService()
