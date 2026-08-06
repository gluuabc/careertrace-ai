from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.outreach import outreach_service
from app.services.resume_revision import resume_revision_service
from app.state.agent_schema import OutreachDraftInput, ResumeRevisionDraftInput, ToolExecutionResult


@tool
def save_resume_revision_draft(
    draft: ResumeRevisionDraftInput,
    user_id: Annotated[str, InjectedState("user_id")],
) -> dict:
    """Save a structured resume revision draft supported by the user's current profile/documents and selected job evidence. This never changes the confirmed profile or original PDF/DOCX and never applies the draft."""
    try:
        saved = resume_revision_service.save(user_id, draft)
        return ToolExecutionResult(ok=True, data=saved).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=str(error)[:500]).model_dump()


@tool
def save_outreach_draft(
    draft: OutreachDraftInput,
    user_id: Annotated[str, InjectedState("user_id")],
) -> dict:
    """Save an evidence-backed outreach draft. This has no sending side effect; status is always draft and the message must be described as not sent."""
    try:
        saved = outreach_service.save(user_id, draft)
        return ToolExecutionResult(ok=True, data=saved).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=str(error)[:500]).model_dump()


@tool
def update_outreach_status(
    draft_id: str,
    status: str,
    user_id: Annotated[str, InjectedState("user_id")],
) -> dict:
    """Move a user-owned outreach draft to ready or archived. The agent cannot mark a draft sent; sent status requires an explicit UI action by the user."""
    if status == "sent":
        return ToolExecutionResult(ok=False, error_type="ApprovalRequired", error_message="Marking sent requires explicit user action in the UI.").model_dump()
    try:
        saved = outreach_service.mark_status(user_id, draft_id, status, explicit_user_action=False)
        return ToolExecutionResult(ok=True, data=saved).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=str(error)[:500]).model_dump()
