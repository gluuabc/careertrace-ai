from __future__ import annotations

import os
from typing import Any, Protocol

import boto3


RERANK_MODEL_ARN = "arn:aws:bedrock:us-west-2::foundation-model/amazon.rerank-v1:0"


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[dict[str, Any]], *, top_n: int = 10) -> list[dict[str, Any]]: ...


class AmazonRerankProvider:
    """Dedicated us-west-2 Bedrock Agent Runtime client for Amazon Rerank 1.0."""

    def __init__(self, client=None):
        self.region = os.getenv("BEDROCK_RERANK_REGION", "us-west-2")
        self.model_id = os.getenv("BEDROCK_RERANK_MODEL_ID", "amazon.rerank-v1:0")
        self.model_arn = f"arn:aws:bedrock:{self.region}::foundation-model/{self.model_id}"
        self.client = client or boto3.client("bedrock-agent-runtime", region_name=self.region)

    def rerank(self, query: str, candidates: list[dict[str, Any]], *, top_n: int = 10) -> list[dict[str, Any]]:
        bounded = candidates[:30]
        if not bounded:
            return []
        response = self.client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": item["rerank_text"]}}} for item in bounded],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": self.model_arn},
                    "numberOfResults": min(top_n, len(bounded)),
                },
            },
        )
        ranked = []
        for rank, result in enumerate(response.get("results") or [], start=1):
            index = int(result["index"])
            if 0 <= index < len(bounded):
                item = dict(bounded[index])
                item["rerank_score"] = float(result["relevanceScore"])
                item["rerank_rank"] = rank
                ranked.append(item)
        return ranked
