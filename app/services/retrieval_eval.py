from __future__ import annotations


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


def reciprocal_rank(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    for rank, identifier in enumerate(ranked_ids, start=1):
        if identifier in relevant_ids:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(rankings: list[list[str]], labels: list[set[str]]) -> float:
    if not rankings:
        return 0.0
    return sum(reciprocal_rank(ranking, relevant) for ranking, relevant in zip(rankings, labels)) / len(rankings)
