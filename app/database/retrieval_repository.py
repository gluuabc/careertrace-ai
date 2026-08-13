from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, delete, or_, select, text, update

from app.database.database import SessionLocal, session_scope
from app.database.models import RetrievalDocument, RetrievalQueryLog, User


TOKEN = re.compile(r"[a-z0-9+#.][a-z0-9_+#.-]*", re.I)
CORPUS_TYPES = {"job", "person", "user_connection", "resume", "project", "uploaded_document_chunk", "approved_memory", "evidence"}


def _tokens(value: str) -> list[str]:
    return [item.casefold() for item in TOKEN.findall(value)]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    denominator = math.sqrt(sum(item * item for item in left)) * math.sqrt(sum(item * item for item in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else -1.0


class RetrievalRepository:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    def upsert_document(
        self,
        *,
        corpus_type: str,
        user_id: str | None,
        source_entity_id: str,
        source_version: str,
        title: str,
        text_content: str,
        metadata: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        retrieved_at: datetime | None = None,
        expires_at: datetime | None = None,
        embedding_model_id: str | None = None,
        embedding_dimension: int | None = None,
        embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        if corpus_type not in CORPUS_TYPES:
            raise ValueError("Unsupported retrieval corpus type.")
        clean_text = text_content.strip()
        if not clean_text:
            raise ValueError("Retrieval document text is required.")
        digest = hashlib.sha256(clean_text.encode()).hexdigest()
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id) if user_id else None
            if user_id and user is None:
                raise ValueError("Retrieval document user was not found.")
            existing = session.scalar(
                select(RetrievalDocument).where(
                    RetrievalDocument.corpus_type == corpus_type,
                    RetrievalDocument.user_id.is_(None) if user_id is None else RetrievalDocument.user_id == user_id,
                    RetrievalDocument.source_entity_id == source_entity_id,
                    RetrievalDocument.source_version == source_version,
                    RetrievalDocument.content_hash == digest,
                )
            )
            if existing:
                existing.title = title.strip() or source_entity_id
                existing.metadata_json = dict(metadata or {})
                existing.evidence_ids = list(dict.fromkeys(evidence_ids or []))
                existing.expires_at = expires_at
                existing.active = True
                if embedding is not None and existing.embedding is None:
                    existing.embedding_model_id = embedding_model_id
                    existing.embedding_dimension = embedding_dimension
                    existing.embedding = embedding
                session.flush()
                return self._document_dict(existing)
            if embedding is None and embedding_model_id and embedding_dimension:
                reusable = session.scalar(
                    select(RetrievalDocument).where(
                        RetrievalDocument.content_hash == digest,
                        RetrievalDocument.embedding_model_id == embedding_model_id,
                        RetrievalDocument.embedding_dimension == embedding_dimension,
                        RetrievalDocument.embedding.is_not(None),
                        or_(RetrievalDocument.user_id.is_(None), RetrievalDocument.user_id == user_id),
                    )
                )
                if reusable:
                    embedding = list(reusable.embedding or [])
            item = RetrievalDocument(
                corpus_type=corpus_type,
                user=user,
                source_entity_id=source_entity_id,
                source_version=source_version,
                title=title.strip() or source_entity_id,
                text=clean_text,
                search_vector=" ".join(_tokens(f"{title} {clean_text}")),
                metadata_json=dict(metadata or {}),
                evidence_ids=list(dict.fromkeys(evidence_ids or [])),
                content_hash=digest,
                retrieved_at=retrieved_at or datetime.now(timezone.utc),
                expires_at=expires_at,
                embedding_model_id=embedding_model_id,
                embedding_dimension=embedding_dimension,
                embedding=embedding,
            )
            session.add(item)
            session.flush()
            return self._document_dict(item)

    def get_cached_embedding(self, user_id: str | None, content_hash: str, model_id: str, dimensions: int) -> list[float] | None:
        with session_scope(self.session_factory) as session:
            item = session.scalar(
                select(RetrievalDocument).where(
                    RetrievalDocument.content_hash == content_hash,
                    RetrievalDocument.embedding_model_id == model_id,
                    RetrievalDocument.embedding_dimension == dimensions,
                    RetrievalDocument.embedding.is_not(None),
                    or_(RetrievalDocument.user_id.is_(None), RetrievalDocument.user_id == user_id),
                )
            )
            return list(item.embedding) if item and item.embedding else None

    def has_documents(self, user_id: str, corpus_types: list[str]) -> bool:
        with session_scope(self.session_factory) as session:
            return session.scalar(
                select(RetrievalDocument.retrieval_document_id)
                .where(
                    or_(RetrievalDocument.user_id.is_(None), RetrievalDocument.user_id == user_id),
                    RetrievalDocument.corpus_type.in_(corpus_types),
                    RetrievalDocument.active.is_(True),
                )
                .limit(1)
            ) is not None

    def _visible_documents(
        self,
        session,
        user_id: str,
        corpus_types: list[str],
        document_ids: list[str] | None = None,
    ):
        now = datetime.now(timezone.utc)
        statement = select(RetrievalDocument).where(
                or_(RetrievalDocument.user_id.is_(None), RetrievalDocument.user_id == user_id),
                RetrievalDocument.corpus_type.in_(corpus_types),
                RetrievalDocument.active.is_(True),
                or_(RetrievalDocument.expires_at.is_(None), RetrievalDocument.expires_at > now),
            )
        if document_ids is not None:
            if not document_ids:
                return []
            statement = statement.where(RetrievalDocument.retrieval_document_id.in_(document_ids))
        return session.scalars(statement).all()

    def sparse_search(self, user_id: str, query: str, corpus_types: list[str], limit: int = 30, document_ids: list[str] | None = None) -> list[tuple[dict[str, Any], float]]:
        with session_scope(self.session_factory) as session:
            if session.bind.dialect.name == "cockroachdb":
                id_clause = " AND retrieval_document_id IN :document_ids" if document_ids is not None else ""
                if document_ids is not None and not document_ids:
                    return []
                statement = text("""
                    SELECT *, ts_rank(search_vector_fts, plainto_tsquery('english', :query)) AS score
                    FROM retrieval_documents
                    WHERE (user_id IS NULL OR user_id = :user_id)
                      AND corpus_type IN :corpora
                      AND active = true
                      AND (expires_at IS NULL OR expires_at > now())
                      AND search_vector_fts @@ plainto_tsquery('english', :query)
                """ + id_clause + " ORDER BY score DESC LIMIT :limit").bindparams(bindparam("corpora", expanding=True))
                params = {"query": query, "user_id": user_id, "corpora": corpus_types, "limit": limit}
                if document_ids is not None:
                    statement = statement.bindparams(bindparam("document_ids", expanding=True))
                    params["document_ids"] = document_ids
                rows = session.execute(statement, params).mappings().all()
                return [(self._mapping_dict(row), float(row["score"])) for row in rows]
            query_tokens = _tokens(query)
            scored = []
            for item in self._visible_documents(session, user_id, corpus_types, document_ids):
                tokens = _tokens(f"{item.title} {item.text}")
                score = sum(tokens.count(token) for token in query_tokens)
                if score:
                    scored.append((self._document_dict(item), float(score)))
            return sorted(scored, key=lambda pair: (-pair[1], pair[0]["retrieval_document_id"]))[:limit]

    def dense_search(self, user_id: str, embedding: list[float], corpus_types: list[str], limit: int = 30, document_ids: list[str] | None = None) -> list[tuple[dict[str, Any], float]]:
        with session_scope(self.session_factory) as session:
            if session.bind.dialect.name == "cockroachdb":
                id_clause = " AND retrieval_document_id IN :document_ids" if document_ids is not None else ""
                if document_ids is not None and not document_ids:
                    return []
                statement = text("""
                    SELECT *, 1 - (embedding <=> CAST(:embedding AS VECTOR)) AS score
                    FROM retrieval_documents
                    WHERE embedding IS NOT NULL
                      AND (user_id IS NULL OR user_id = :user_id)
                      AND corpus_type IN :corpora
                      AND active = true
                      AND (expires_at IS NULL OR expires_at > now())
                """ + id_clause + " ORDER BY embedding <=> CAST(:embedding AS VECTOR) LIMIT :limit").bindparams(bindparam("corpora", expanding=True))
                params = {"embedding": json.dumps(embedding), "user_id": user_id, "corpora": corpus_types, "limit": limit}
                if document_ids is not None:
                    statement = statement.bindparams(bindparam("document_ids", expanding=True))
                    params["document_ids"] = document_ids
                rows = session.execute(statement, params).mappings().all()
                return [(self._mapping_dict(row), float(row["score"])) for row in rows]
            scored = [(self._document_dict(item), _cosine(embedding, list(item.embedding or []))) for item in self._visible_documents(session, user_id, corpus_types, document_ids) if item.embedding]
            return sorted(scored, key=lambda pair: (-pair[1], pair[0]["retrieval_document_id"]))[:limit]

    def save_query_debug(self, user_id: str, query: str, corpus_types: list[str], rankings: list[dict[str, Any]], warnings: list[str]) -> str:
        if os.getenv("RETRIEVAL_DEBUG_LOGGING", "false").strip().casefold() not in {"1", "true", "yes", "on"}:
            return ""
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("Retrieval query user was not found.")
            item = RetrievalQueryLog(user=user, query=query, corpus_types=corpus_types, rankings_json=rankings, warnings=warnings)
            session.add(item)
            session.flush()
            return item.retrieval_query_id

    def list_missing_embeddings(self, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            requested = max(1, min(limit, 1000))
            statement = select(RetrievalDocument).where(
                RetrievalDocument.user_id == user_id,
                RetrievalDocument.active.is_(True),
            )
            # SQLAlchemy's SQLite JSON type persists Python None as JSON null,
            # which is not matched by SQL ``IS NULL``. Production vectors use
            # real SQL NULL, while this small Python filter keeps local/test
            # backfill behavior equivalent.
            if session.bind.dialect.name != "sqlite":
                statement = statement.where(RetrievalDocument.embedding.is_(None))
            items = session.scalars(
                statement.order_by(RetrievalDocument.created_at, RetrievalDocument.retrieval_document_id)
            ).all()
            items = [item for item in items if item.embedding is None][:requested]
            return [self._document_dict(item) for item in items]

    def set_embedding(self, user_id: str, retrieval_document_id: str, *, model_id: str, dimensions: int, embedding: list[float]) -> bool:
        with session_scope(self.session_factory) as session:
            item = session.scalar(select(RetrievalDocument).where(
                RetrievalDocument.retrieval_document_id == retrieval_document_id,
                RetrievalDocument.user_id == user_id,
            ).with_for_update())
            if item is None:
                raise ValueError("Retrieval document was not found for this user.")
            # SQLite's portable vector type can deserialize SQL NULL as an
            # empty list. Treat only a populated vector as already embedded.
            if item.embedding:
                return False
            item.embedding_model_id = model_id
            item.embedding_dimension = dimensions
            item.embedding = embedding
            session.flush()
            return True

    def deactivate_source(self, user_id: str, *, corpus_types: list[str], source_entity_prefix: str) -> int:
        with session_scope(self.session_factory) as session:
            result = session.execute(
                update(RetrievalDocument)
                .where(
                    RetrievalDocument.user_id == user_id,
                    RetrievalDocument.corpus_type.in_(corpus_types),
                    RetrievalDocument.source_entity_id.like(f"{source_entity_prefix}%"),
                    RetrievalDocument.active.is_(True),
                )
                .values(active=False)
            )
            return int(result.rowcount or 0)

    def deactivate_other_versions(self, user_id: str, *, corpus_types: list[str], active_source_version: str) -> int:
        with session_scope(self.session_factory) as session:
            result = session.execute(
                update(RetrievalDocument)
                .where(
                    RetrievalDocument.user_id == user_id,
                    RetrievalDocument.corpus_type.in_(corpus_types),
                    RetrievalDocument.source_version != active_source_version,
                    RetrievalDocument.active.is_(True),
                )
                .values(active=False)
            )
            return int(result.rowcount or 0)

    @staticmethod
    def _document_dict(item: RetrievalDocument) -> dict[str, Any]:
        return {"retrieval_document_id": item.retrieval_document_id, "corpus_type": item.corpus_type, "user_id": item.user_id, "source_entity_id": item.source_entity_id, "source_version": item.source_version, "title": item.title, "text": item.text, "metadata": dict(item.metadata_json), "evidence_ids": list(item.evidence_ids), "content_hash": item.content_hash, "retrieved_at": item.retrieved_at.isoformat(), "expires_at": item.expires_at.isoformat() if item.expires_at else None, "embedding_model_id": item.embedding_model_id, "embedding_dimension": item.embedding_dimension, "active": item.active}

    @staticmethod
    def _mapping_dict(row) -> dict[str, Any]:
        return {"retrieval_document_id": row["retrieval_document_id"], "corpus_type": row["corpus_type"], "user_id": row["user_id"], "source_entity_id": row["source_entity_id"], "source_version": row["source_version"], "title": row["title"], "text": row["text"], "metadata": row["metadata_json"] or {}, "evidence_ids": row["evidence_ids"] or [], "content_hash": row["content_hash"]}


retrieval_repository = RetrievalRepository()
