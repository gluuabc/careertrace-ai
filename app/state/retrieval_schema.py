from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalHit(BaseModel):
    retrieval_document_id: str
    corpus_type: str
    title: str
    text_excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    sparse_rank: int | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    dense_score: float | None = None
    rrf_score: float
    rerank_score: float | None = None
    rerank_rank: int | None = None


class RetrievalLoopState(BaseModel):
    original_query: str
    query_variants: list[str] = Field(default_factory=list)
    active_query: str
    retrieved_ids: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    sufficiency: dict[str, Any] = Field(default_factory=dict)
    remaining_source_budget: int = 0
    has_more_sources: bool = False
    has_more_pages: bool = False
    iteration: int = 0
    max_iterations: int = 3


class HybridRetrievalResult(BaseModel):
    items: list[RetrievalHit] = Field(default_factory=list)
    state: RetrievalLoopState
    warnings: list[str] = Field(default_factory=list)
