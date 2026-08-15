from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from bs4 import BeautifulSoup

from app.database.repository import ProfileRepository, profile_repository
from app.storage.base import ObjectStorage
from app.storage.s3 import S3ObjectStorage, StorageError

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SQL_FALLBACK_LIMIT = 256 * 1024


def sanitize_external_content(content: str, content_type: str) -> str:
    text = content
    if "html" in content_type.casefold():
        soup = BeautifulSoup(content, "html.parser")
        for element in soup(["script", "style", "noscript", "iframe"]):
            element.decompose()
        text = soup.get_text("\n", strip=True)
    text = CONTROL_CHARACTERS.sub("", text)
    return text[:2_000_000]


class EvidenceService:
    def __init__(
        self,
        repository: ProfileRepository = profile_repository,
        storage: ObjectStorage | None = None,
    ):
        self.repository = repository
        self.storage = storage or S3ObjectStorage()

    def store(
        self,
        *,
        user_id: str,
        run_id: str,
        source_type: str,
        source_name: str,
        source_url: str | None,
        content_type: str,
        raw_content: str,
        structured_content: dict[str, Any] | None = None,
        index_for_retrieval: bool = True,
        phase_observer=None,
    ) -> tuple[dict[str, Any], list[str]]:
        sanitized = sanitize_external_content(raw_content, content_type)
        raw_bytes = sanitized.encode("utf-8")
        evidence_id = f"ev_{uuid4()}"
        digest = hashlib.sha256(raw_bytes).hexdigest()
        threshold = int(os.getenv("EVIDENCE_S3_THRESHOLD_BYTES", "65536"))
        s3_enabled = os.getenv(
            "EVIDENCE_S3_ENABLED", "true" if os.getenv("S3_BUCKET_NAME") else "false"
        ).casefold() in {"1", "true", "yes", "on"}
        backend = "sql"
        storage_key = None
        sql_raw: str | None = sanitized
        warnings: list[str] = []
        if len(raw_bytes) > threshold and s3_enabled:
            extension = "html" if "html" in content_type.casefold() else "json"
            storage_key = (
                f"agent-evidence/{user_id}/{run_id}/{evidence_id}.{extension}.gz"
            )
            try:
                storage_started = perf_counter()
                self.storage.put(
                    storage_key, gzip.compress(raw_bytes), "application/gzip"
                )
                if phase_observer:
                    phase_observer(
                        "evidence_object_storage",
                        round((perf_counter() - storage_started) * 1000),
                        candidate_count=1,
                    )
                backend = "s3"
                sql_raw = None
            except StorageError as error:
                warnings.append(f"Evidence S3 storage unavailable: {error}")
                storage_key = None
                if len(raw_bytes) > SQL_FALLBACK_LIMIT:
                    raise StorageError(
                        "Evidence exceeded the safe SQL fallback limit."
                    ) from error
        sql_started = perf_counter()
        record = self.repository.create_evidence(
            user_id,
            run_id,
            evidence_id=evidence_id,
            source_type=source_type,
            source_name=source_name,
            source_url=source_url,
            content_type=content_type,
            content_excerpt=sanitized[:2000],
            structured_content=structured_content,
            content_hash=digest,
            raw_content=sql_raw,
            raw_size_bytes=len(raw_bytes),
            storage_backend=backend,
            storage_key=storage_key,
        )
        if phase_observer:
            phase_observer(
                "evidence_sql_persistence",
                round((perf_counter() - sql_started) * 1000),
                candidate_count=1,
            )
        try:
            if not index_for_retrieval:
                return record, warnings
            indexing_started = perf_counter()
            from app.database.retrieval_repository import RetrievalRepository
            from app.services.retrieval_corpus import RetrievalCorpusIndexer

            RetrievalCorpusIndexer(
                RetrievalRepository(self.repository.session_factory)
            ).index_evidence(
                user_id=user_id,
                run_id=run_id,
                evidence_id=evidence_id,
                source_name=source_name,
                source_type=source_type,
                source_url=source_url,
                content_hash=digest,
                text=sanitized,
            )
            if phase_observer:
                phase_observer(
                    "evidence_retrieval_indexing",
                    round((perf_counter() - indexing_started) * 1000),
                    candidate_count=1,
                )
        except Exception:
            warnings.append("Evidence retrieval indexing is temporarily unavailable.")
        return record, warnings


evidence_service = EvidenceService()
