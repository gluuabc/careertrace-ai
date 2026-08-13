from __future__ import annotations

import json
from typing import Any

from app.database.retrieval_repository import RetrievalRepository, retrieval_repository
from app.services.retrieval import HybridRetrievalService


class RetrievalCorpusIndexer:
    """Canonical ingestion boundary for every production retrieval corpus."""

    def __init__(
        self,
        repository: RetrievalRepository = retrieval_repository,
        retrieval_service: HybridRetrievalService | None = None,
    ):
        self.repository = repository
        self.retrieval_service = retrieval_service or HybridRetrievalService(repository)

    def index_text(self, **kwargs: Any) -> list[dict[str, Any]]:
        # HybridRetrievalService persists sparse text even when Titan is unavailable.
        return self.retrieval_service.index_text(**kwargs)

    def index_uploaded_document(self, *, user_id: str, document_id: str, document_type: str, filename: str, text: str) -> list[dict[str, Any]]:
        return self.index_text(
            corpus_type="uploaded_document_chunk",
            user_id=user_id,
            source_entity_id=document_id,
            source_version="1",
            title=filename,
            text=text,
            metadata={"document_id": document_id, "document_type": document_type},
        )

    def index_profile(self, *, user_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
        version = str(profile.get("profile_version_id") or profile.get("profile_version") or "current")
        self.repository.deactivate_other_versions(
            user_id,
            corpus_types=["resume", "project"],
            active_source_version=version,
        )
        records = self.index_text(
            corpus_type="resume",
            user_id=user_id,
            source_entity_id=str(profile.get("profile_version_id") or "profile"),
            source_version=version,
            title="Confirmed career profile",
            text=json.dumps({key: profile.get(key) for key in ("education", "school", "major", "graduation_year", "skills", "experience")}, ensure_ascii=False),
            metadata={"profile_version_id": profile.get("profile_version_id"), "current_profile": True},
        )
        for index, project in enumerate(profile.get("projects") or []):
            text = json.dumps(project, ensure_ascii=False) if isinstance(project, dict) else str(project)
            records.extend(self.index_text(
                corpus_type="project",
                user_id=user_id,
                source_entity_id=f"{profile.get('profile_version_id') or 'profile'}:project:{index}",
                source_version=version,
                title=str(project.get("title") or f"Project {index + 1}") if isinstance(project, dict) else f"Project {index + 1}",
                text=text,
                metadata={"profile_version_id": profile.get("profile_version_id"), "project_index": index, "current_profile": True},
            ))
        return records

    def index_memory(self, *, user_id: str, memory: dict[str, Any]) -> list[dict[str, Any]]:
        return self.index_text(
            corpus_type="approved_memory",
            user_id=user_id,
            source_entity_id=memory["memory_id"],
            source_version="1",
            title=memory["category"],
            text=memory["content"],
            metadata={"source": memory.get("source")},
        )

    def index_evidence(self, *, user_id: str, run_id: str, evidence_id: str, source_name: str, source_type: str, source_url: str | None, content_hash: str, text: str) -> list[dict[str, Any]]:
        return self.index_text(
            corpus_type="evidence",
            user_id=user_id,
            source_entity_id=evidence_id,
            source_version=content_hash,
            title=source_name,
            text=text,
            metadata={"run_id": run_id, "source_type": source_type, "source_url": source_url},
            evidence_ids=[evidence_id],
        )

    def index_candidate(self, *, corpus_type: str, user_id: str, search_session_id: str, run_id: str, candidate_id: str, title: str, text: str, metadata: dict[str, Any], evidence_ids: list[str]) -> list[dict[str, Any]]:
        if corpus_type not in {"job", "person"}:
            raise ValueError("Candidate corpus type must be job or person.")
        return self.index_text(
            corpus_type=corpus_type,
            user_id=user_id,
            source_entity_id=candidate_id,
            source_version=search_session_id,
            title=title,
            text=text,
            metadata={**metadata, "search_session_id": search_session_id, "run_id": run_id, "candidate_id": candidate_id},
            evidence_ids=evidence_ids,
        )

    def backfill_missing_embeddings(self, *, user_id: str, limit: int = 100) -> dict[str, int]:
        return self.retrieval_service.backfill_missing_embeddings(user_id=user_id, limit=limit)


retrieval_corpus_indexer = RetrievalCorpusIndexer()
