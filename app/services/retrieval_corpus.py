from __future__ import annotations

import json
from typing import Any

from app.database.retrieval_repository import RetrievalRepository, retrieval_repository
from app.services.embeddings import logical_chunks


class RetrievalCorpusIndexer:
    """Deterministic sparse-first indexing; embeddings can be added/reused later."""

    def __init__(self, repository: RetrievalRepository = retrieval_repository):
        self.repository = repository

    def index_uploaded_document(self, *, user_id: str, document_id: str, document_type: str, filename: str, text: str) -> list[dict[str, Any]]:
        records = []
        for index, chunk in enumerate(logical_chunks(text)):
            records.append(self.repository.upsert_document(corpus_type="uploaded_document_chunk", user_id=user_id, source_entity_id=f"{document_id}:{index}", source_version="1", title=filename, text_content=chunk, metadata={"document_id": document_id, "document_type": document_type, "chunk_index": index}))
        return records

    def index_profile(self, *, user_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
        version = str(profile.get("profile_version_id") or profile.get("profile_version") or "current")
        records = [self.repository.upsert_document(corpus_type="resume", user_id=user_id, source_entity_id=str(profile.get("profile_version_id") or "profile"), source_version=version, title="Confirmed career profile", text_content=json.dumps({key: profile.get(key) for key in ("education", "school", "major", "graduation_year", "skills", "experience")}, ensure_ascii=False), metadata={"profile_version_id": profile.get("profile_version_id")})]
        for index, project in enumerate(profile.get("projects") or []):
            text = json.dumps(project, ensure_ascii=False) if isinstance(project, dict) else str(project)
            records.append(self.repository.upsert_document(corpus_type="project", user_id=user_id, source_entity_id=f"{profile.get('profile_version_id') or 'profile'}:project:{index}", source_version=version, title=str(project.get("title") or f"Project {index + 1}") if isinstance(project, dict) else f"Project {index + 1}", text_content=text, metadata={"profile_version_id": profile.get("profile_version_id"), "project_index": index}))
        return records


retrieval_corpus_indexer = RetrievalCorpusIndexer()
