from __future__ import annotations

import re
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.state.agent_schema import ResumeRevisionDraftInput

NUMBER = re.compile(r"\b\d+(?:\.\d+)?%?\b")


class ResumeRevisionService:
    def __init__(self, repository: ProfileRepository = profile_repository):
        self.repository = repository

    def save(self, user_id: str, draft: ResumeRevisionDraftInput) -> dict[str, Any]:
        data = draft.model_dump()
        for change in data["changes"]:
            original_numbers = set(NUMBER.findall(change.get("original_text") or ""))
            proposed_numbers = set(NUMBER.findall(change["proposed_text"]))
            unsupported = proposed_numbers - original_numbers
            if unsupported and not change.get("profile_evidence_ids"):
                change["warnings"].append(
                    "Proposed numeric detail needs user confirmation: "
                    + ", ".join(sorted(unsupported))
                )
        return self.repository.save_resume_revision_draft(user_id, data)


resume_revision_service = ResumeRevisionService()
