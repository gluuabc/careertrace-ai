from __future__ import annotations

import json
import os
import re
from typing import Protocol

import boto3


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


def logical_chunks(text: str, *, max_words: int = 350) -> list[str]:
    """Chunk on paragraphs/sections, only grouping whole logical blocks."""
    paragraphs = [" ".join(item.split()) for item in re.split(r"\n\s*\n+", text) if item.strip()]
    chunks: list[str] = []
    current: list[str] = []
    words = 0
    for paragraph in paragraphs:
        paragraph_words = len(paragraph.split())
        if current and words + paragraph_words > max_words:
            chunks.append("\n\n".join(current))
            current, words = [], 0
        current.append(paragraph)
        words += paragraph_words
    if current:
        chunks.append("\n\n".join(current))
    return chunks or ([text.strip()] if text.strip() else [])


class TitanEmbeddingProvider:
    def __init__(self, client=None):
        self.model_id = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
        self.dimensions = int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024"))
        self.client = client or boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))

    def embed(self, text: str) -> list[float]:
        response = self.client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True}),
        )
        body = json.loads(response["body"].read())
        vector = [float(item) for item in body["embedding"]]
        if len(vector) != self.dimensions:
            raise ValueError("Bedrock returned an unexpected embedding dimension.")
        return vector
