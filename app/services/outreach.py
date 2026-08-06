from __future__ import annotations

from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.state.agent_schema import OutreachDraftInput


class OutreachService:
    def __init__(self, repository: ProfileRepository = profile_repository):
        self.repository = repository

    def save(self, user_id: str, draft: OutreachDraftInput) -> dict[str, Any]:
        return self.repository.save_outreach_draft(user_id, draft.model_dump())

    def mark_status(
        self,
        user_id: str,
        draft_id: str,
        status: str,
        *,
        explicit_user_action: bool,
    ) -> dict[str, Any]:
        return self.repository.update_outreach_status(
            user_id, draft_id, status, explicit_user_action=explicit_user_action
        )


outreach_service = OutreachService()
