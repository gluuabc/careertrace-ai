from __future__ import annotations

import gzip
import json
from typing import Any

from app.database.repository import profile_repository
from app.services.evidence import evidence_service


def candidate_detail_page(
    *,
    user_id: str,
    run_id: str,
    candidate: dict[str, Any],
    offset: int,
    limit: int,
) -> tuple[dict[str, Any], list[str]]:
    if offset < 0 or limit < 1 or limit > 8000:
        raise ValueError("Detail offset must be non-negative and limit must be 1–8000.")
    evidence_ids = list(dict.fromkeys(candidate.get("evidence_ids") or []))
    sections = [json.dumps(candidate, ensure_ascii=False, indent=2)]
    for evidence_id in evidence_ids:
        evidence = profile_repository.get_evidence(user_id, evidence_id)
        if evidence["run_id"] != run_id:
            raise ValueError("Candidate evidence is not linked to this run.")
        content = evidence.get("raw_content")
        if content is None and evidence.get("storage_backend") == "s3":
            content = gzip.decompress(evidence_service.storage.get(evidence["storage_key"])).decode("utf-8", errors="replace")
        sections.append(f"\n[EVIDENCE {evidence_id}]\n{content or evidence.get('content_excerpt') or ''}")
    content = "\n".join(sections)
    page = content[offset : offset + limit]
    next_offset = offset + len(page)
    return {
        "content": page,
        "offset": offset,
        "limit": limit,
        "returned_count": len(page),
        "total_count": len(content),
        "has_more": next_offset < len(content),
        "next_offset": next_offset if next_offset < len(content) else None,
        "truncated": next_offset < len(content),
        "candidate": candidate,
    }, evidence_ids
