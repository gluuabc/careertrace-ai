from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.config import Config


CHUNKING_STRATEGY = "recursive_structure_v1"
HEADING = re.compile(r"(?m)^(?:#{1,6}\s+.+|[A-Z][A-Za-z /&-]{2,60}:?)\s*$")
SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")


def estimate_embedding_tokens(text: str) -> int:
    """Conservative planning estimate only; Titan telemetry remains authoritative."""
    if not text:
        return 0
    ascii_chars = sum(ord(char) < 128 for char in text)
    non_ascii = len(text) - ascii_chars
    return max(1, (ascii_chars + 2) // 3 + non_ascii)


@dataclass(frozen=True)
class RetrievalChunk:
    text: str
    chunk_index: int
    section_title: str | None
    section_path: list[str]
    start_offset: int | None
    end_offset: int | None
    token_count: int
    token_count_source: str
    chunking_strategy: str = CHUNKING_STRATEGY


def _hard_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    if not text:
        return []
    max_chars = max(1, max_tokens * 3)
    overlap_chars = min(max_chars - 1, overlap_tokens * 3)
    parts = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        while end > start + 1 and estimate_embedding_tokens(text[start:end]) > max_tokens:
            end -= 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return [part for part in parts if part]


def _split_oversized(block: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    if estimate_embedding_tokens(block) <= max_tokens:
        return [block]
    sentences = [item.strip() for item in SENTENCE.split(block) if item.strip()]
    if len(sentences) > 1:
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            proposed = f"{current} {sentence}".strip()
            if current and estimate_embedding_tokens(proposed) > max_tokens:
                parts.extend(_hard_split(current, max_tokens, overlap_tokens) if estimate_embedding_tokens(current) > max_tokens else [current])
                current = sentence
            else:
                current = proposed
        if current:
            parts.extend(_hard_split(current, max_tokens, overlap_tokens) if estimate_embedding_tokens(current) > max_tokens else [current])
        return parts
    return _hard_split(block, max_tokens, overlap_tokens)


def recursive_structure_chunks(
    text: str,
    *,
    target_tokens: int | None = None,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[RetrievalChunk]:
    target = target_tokens or int(os.getenv("RETRIEVAL_CHUNK_TARGET_TOKENS", "512"))
    maximum = max_tokens or int(os.getenv("RETRIEVAL_CHUNK_MAX_TOKENS", "768"))
    overlap = overlap_tokens if overlap_tokens is not None else int(os.getenv("RETRIEVAL_CHUNK_OVERLAP_TOKENS", "64"))
    clean = text.strip()
    if not clean:
        return []
    blocks = [item.strip() for item in re.split(r"\n\s*\n+", clean) if item.strip()]
    output: list[tuple[str, str | None]] = []
    current: list[str] = []
    section: str | None = None
    for block in blocks:
        if HEADING.fullmatch(block):
            if current:
                output.append(("\n\n".join(current), section))
                current = []
            section = block.lstrip("# ").rstrip(":").strip()
            current = [block]
            continue
        split_blocks = _split_oversized(block, maximum, overlap)
        for piece in split_blocks:
            proposed = "\n\n".join([*current, piece])
            if current and estimate_embedding_tokens(proposed) > target:
                output.append(("\n\n".join(current), section))
                current = [piece]
            else:
                current.append(piece)
    if current:
        output.append(("\n\n".join(current), section))
    chunks: list[RetrievalChunk] = []
    search_offset = 0
    for index, (value, title) in enumerate(output):
        if estimate_embedding_tokens(value) > maximum:
            forced = _hard_split(value, maximum, overlap)
        else:
            forced = [value]
        for part in forced:
            start = clean.find(part, search_offset)
            if start < 0:
                start = None
                end = None
            else:
                end = start + len(part)
                search_offset = max(search_offset, end)
            chunks.append(RetrievalChunk(
                text=part,
                chunk_index=len(chunks),
                section_title=title,
                section_path=[title] if title else [],
                start_offset=start,
                end_offset=end,
                token_count=estimate_embedding_tokens(part),
                token_count_source="heuristic_fallback",
            ))
    return chunks


def logical_chunks(text: str, *, max_words: int = 350) -> list[str]:
    """Compatibility wrapper using the canonical token-bounded strategy."""
    return [chunk.text for chunk in recursive_structure_chunks(text)]


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int
    last_input_tokens: int | None

    def embed(self, text: str) -> list[float]: ...


class TitanEmbeddingProvider:
    def __init__(self, client=None):
        self.model_id = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
        self.dimensions = int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024"))
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            config=Config(
                connect_timeout=float(os.getenv("BEDROCK_CONNECT_TIMEOUT_SECONDS", "3")),
                read_timeout=float(os.getenv("BEDROCK_READ_TIMEOUT_SECONDS", "10")),
                retries={"max_attempts": int(os.getenv("BEDROCK_MAX_ATTEMPTS", "2")), "mode": "standard"},
            ),
        )
        self.last_input_tokens: int | None = None

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
        count = body.get("inputTextTokenCount")
        self.last_input_tokens = int(count) if count is not None else None
        return vector
