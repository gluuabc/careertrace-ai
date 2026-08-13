from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.database.retrieval_repository import RetrievalRepository, retrieval_repository
from app.services.embeddings import EmbeddingProvider, TitanEmbeddingProvider, recursive_structure_chunks
from app.services.reranking import AmazonRerankProvider, Reranker
from app.state.retrieval_schema import HybridRetrievalResult, RetrievalHit, RetrievalLoopState


def reciprocal_rank_fusion(
    sparse: list[tuple[dict[str, Any], float]],
    dense: list[tuple[dict[str, Any], float]],
    *,
    k: int = 60,
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for channel, results in (("sparse", sparse), ("dense", dense)):
        for rank, (document, score) in enumerate(results, start=1):
            identifier = document["retrieval_document_id"]
            item = fused.setdefault(identifier, {"document": document, "rrf_score": 0.0})
            item[f"{channel}_rank"] = rank
            item[f"{channel}_score"] = float(score)
            item["rrf_score"] += 1.0 / (k + rank)
    return sorted(fused.values(), key=lambda item: (-item["rrf_score"], item["document"]["retrieval_document_id"]))


class HybridRetrievalService:
    def __init__(
        self,
        repository: RetrievalRepository = retrieval_repository,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
    ):
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.reranker = reranker

    def _embedding_provider(self) -> EmbeddingProvider:
        if self.embedding_provider is None:
            self.embedding_provider = TitanEmbeddingProvider()
        return self.embedding_provider

    def index_text(
        self,
        *,
        corpus_type: str,
        user_id: str | None,
        source_entity_id: str,
        source_version: str,
        title: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        provider: EmbeddingProvider | None = None
        provider_warning: str | None = None
        try:
            provider = self._embedding_provider()
        except Exception:
            provider_warning = "Embedding provider is unavailable; sparse indexing was preserved."
        indexed = []
        self.repository.deactivate_source(
            user_id,
            corpus_types=[corpus_type],
            source_entity_prefix=f"{source_entity_id}:chunk:",
        )
        source_content_hash = hashlib.sha256(text.encode()).hexdigest()
        maximum = int(os.getenv("RETRIEVAL_CHUNK_MAX_TOKENS", "768"))
        overlap = int(os.getenv("RETRIEVAL_CHUNK_OVERLAP_TOKENS", "64"))
        pending = list(recursive_structure_chunks(text))
        while pending:
            chunk_info = pending.pop(0)
            chunk = chunk_info.text
            digest = hashlib.sha256(chunk.encode()).hexdigest()
            embedding = None
            embedding_input_tokens = None
            if provider is not None:
                try:
                    embedding = self.repository.get_cached_embedding(user_id, digest, provider.model_id, provider.dimensions)
                    if embedding is None:
                        embedding = provider.embed(chunk)
                        embedding_input_tokens = getattr(provider, "last_input_tokens", None)
                except Exception:
                    provider_warning = "Embedding generation is unavailable; sparse indexing was preserved."
            if embedding_input_tokens is not None and embedding_input_tokens > maximum and len(chunk) > 1:
                # The Titan response is authoritative. Re-plan this one chunk with
                # a proportionally smaller conservative limit and never persist
                # the known-oversized representation as canonical.
                adjusted_max = max(
                    1,
                    int(chunk_info.token_count * maximum / embedding_input_tokens * 0.9),
                )
                children = recursive_structure_chunks(
                    chunk,
                    target_tokens=adjusted_max,
                    max_tokens=adjusted_max,
                    overlap_tokens=min(overlap, max(0, adjusted_max - 1)),
                )
                if len(children) > 1:
                    pending = children + pending
                    continue
                provider_warning = "Titan reported an oversized irreducible chunk; sparse indexing was preserved."
                embedding = None
                embedding_input_tokens = None
            index = len(indexed)
            indexed.append(
                self.repository.upsert_document(
                    corpus_type=corpus_type,
                    user_id=user_id,
                    source_entity_id=f"{source_entity_id}:chunk:{index}",
                    source_version=source_version,
                    title=title,
                    text_content=chunk,
                    metadata={
                        **(metadata or {}),
                        "chunk_index": index,
                        "section_title": chunk_info.section_title,
                        "section_path": chunk_info.section_path,
                        "start_offset": chunk_info.start_offset,
                        "end_offset": chunk_info.end_offset,
                        "token_count": embedding_input_tokens or chunk_info.token_count,
                        "token_count_source": "titan_response" if embedding_input_tokens is not None else chunk_info.token_count_source,
                        "chunking_strategy": chunk_info.chunking_strategy,
                        "source_content_hash": source_content_hash,
                        "embedding_input_tokens": embedding_input_tokens,
                        "embedding_model_id": provider.model_id if provider else None,
                        **({"indexing_warning": provider_warning} if provider_warning else {}),
                    },
                    evidence_ids=evidence_ids,
                    embedding_model_id=provider.model_id if provider else None,
                    embedding_dimension=provider.dimensions if provider else None,
                    embedding=embedding,
                )
            )
        return indexed

    def backfill_missing_embeddings(self, *, user_id: str, limit: int = 100) -> dict[str, int]:
        provider = self._embedding_provider()
        populated = 0
        reused = 0
        examined = 0
        for document in self.repository.list_missing_embeddings(user_id, limit=limit):
            examined += 1
            embedding = self.repository.get_cached_embedding(
                user_id, document["content_hash"], provider.model_id, provider.dimensions
            )
            if embedding is None:
                embedding = provider.embed(document["text"])
            else:
                reused += 1
            if self.repository.set_embedding(
                user_id,
                document["retrieval_document_id"],
                model_id=provider.model_id,
                dimensions=provider.dimensions,
                embedding=embedding,
            ):
                populated += 1
        return {"examined": examined, "populated": populated, "reused": reused}

    def retrieve(
        self,
        *,
        user_id: str,
        query: str,
        corpus_types: list[str],
        top_k: int = 10,
        document_ids: list[str] | None = None,
    ) -> HybridRetrievalResult:
        warnings: list[str] = []
        query_embedding: list[float] | None = None
        try:
            query_embedding = self._embedding_provider().embed(query)
        except Exception:
            warnings.append("Dense retrieval is unavailable; sparse retrieval remains active.")
        with ThreadPoolExecutor(max_workers=2) as pool:
            sparse_future = pool.submit(self.repository.sparse_search, user_id, query, corpus_types, 30, document_ids)
            dense_future = pool.submit(self.repository.dense_search, user_id, query_embedding, corpus_types, 30, document_ids) if query_embedding else None
            sparse = sparse_future.result()
            dense = dense_future.result() if dense_future else []
        fused = reciprocal_rank_fusion(sparse, dense)[:30]
        rerank_input = [{**item, "rerank_text": f"{item['document']['title']}\n{item['document']['text']}"[:8000]} for item in fused]
        ranked = rerank_input
        rerank_enabled = os.getenv("BEDROCK_RERANK_ENABLED", "false").strip().casefold() == "true"
        if rerank_enabled:
            try:
                reranker = self.reranker or AmazonRerankProvider()
                reranked = reranker.rerank(query, rerank_input, top_n=min(top_k, 10))
                if not reranked or any(item.get("rerank_rank") is None for item in reranked):
                    raise ValueError("Reranker returned no results.")
                ranked = reranked
            except Exception:
                warnings.append("Amazon Rerank is unavailable; RRF ordering was used.")
                ranked = rerank_input
        else:
            warnings.append("Amazon Rerank is disabled; RRF ordering was used.")
        selected = ranked[: min(top_k, 10)]
        hits = [
            RetrievalHit(
                retrieval_document_id=item["document"]["retrieval_document_id"],
                corpus_type=item["document"]["corpus_type"],
                title=item["document"]["title"],
                text_excerpt=item["document"]["text"][:500],
                metadata=item["document"].get("metadata") or {},
                evidence_ids=item["document"].get("evidence_ids") or [],
                sparse_rank=item.get("sparse_rank"),
                dense_rank=item.get("dense_rank"),
                sparse_score=item.get("sparse_score"),
                dense_score=item.get("dense_score"),
                rrf_score=item["rrf_score"],
                rerank_score=item.get("rerank_score"),
                rerank_rank=item.get("rerank_rank"),
            )
            for item in selected
        ]
        debug = [{key: item.get(key) for key in ("sparse_rank", "dense_rank", "sparse_score", "dense_score", "rrf_score", "rerank_score", "rerank_rank")} | {"retrieval_document_id": item["document"]["retrieval_document_id"]} for item in ranked]
        try:
            self.repository.save_query_debug(user_id, query, corpus_types, debug, warnings)
        except Exception:
            warnings.append("Retrieval debug logging failed without affecting results.")
        state = RetrievalLoopState(original_query=query, query_variants=[query], active_query=query, retrieved_ids=[item["document"]["retrieval_document_id"] for item in fused], selected_ids=[item.retrieval_document_id for item in hits], sufficiency={"requested_count": top_k, "selected_count": len(hits), "sufficient": len(hits) >= min(top_k, len(fused))}, has_more_pages=len(fused) > len(hits), iteration=1)
        return HybridRetrievalResult(items=hits, state=state, warnings=warnings)

    def retrieve_iteratively(
        self,
        *,
        user_id: str,
        query: str,
        corpus_types: list[str],
        desired_count: int = 5,
        max_iterations: int = 3,
        query_rewriter=None,
    ) -> HybridRetrievalResult:
        """Bounded agentic loop: code owns termination; a caller may propose rewrites."""
        variants = [query]
        active = query
        merged: dict[str, RetrievalHit] = {}
        warnings: list[str] = []
        consecutive_no_progress = 0
        last_result: HybridRetrievalResult | None = None
        for iteration in range(1, max_iterations + 1):
            result = self.retrieve(user_id=user_id, query=active, corpus_types=corpus_types, top_k=10)
            last_result = result
            before = len(merged)
            for item in result.items:
                merged.setdefault(item.retrieval_document_id, item)
            consecutive_no_progress = consecutive_no_progress + 1 if len(merged) == before else 0
            warnings.extend(result.warnings)
            if len(merged) >= desired_count or consecutive_no_progress >= 1:
                break
            if query_rewriter is None or iteration >= max_iterations:
                break
            proposed = str(query_rewriter(active, list(merged.values())) or "").strip()
            if not proposed or proposed in variants:
                break
            variants.append(proposed)
            active = proposed
        items = list(merged.values())[:10]
        state = RetrievalLoopState(
            original_query=query,
            query_variants=variants,
            active_query=active,
            retrieved_ids=list(merged),
            selected_ids=[item.retrieval_document_id for item in items],
            sufficiency={
                "requested_count": desired_count,
                "selected_count": len(items),
                "sufficient": len(items) >= desired_count,
                "stop_reason": "sufficient" if len(items) >= desired_count else ("no_progress" if consecutive_no_progress else "options_exhausted"),
            },
            has_more_pages=bool(last_result and last_result.state.has_more_pages),
            iteration=len(variants),
            max_iterations=max_iterations,
        )
        return HybridRetrievalResult(items=items, state=state, warnings=list(dict.fromkeys(warnings)))


hybrid_retrieval_service = HybridRetrievalService()
