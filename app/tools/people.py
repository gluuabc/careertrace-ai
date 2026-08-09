from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.people_search import people_search_service
from app.services.errors import safe_provider_message
from app.database.repository import profile_repository
from app.state.agent_schema import CareerIntent, PeopleSearchRequest, ToolExecutionResult
from app.tools.detail import candidate_detail_page


@tool
def search_people(
    request: PeopleSearchRequest,
    user_id: Annotated[str, InjectedState("user_id")],
    run_id: Annotated[str, InjectedState("run_id")],
    total_source_calls: Annotated[int, InjectedState("total_source_calls")],
) -> dict:
    """Search user-owned connections and permitted public sources for alumni, professors, or verified recruiters. Never scrape LinkedIn or infer private contact information. Public evidence is stored for returned external identities."""
    try:
        import os

        remaining = max(0, int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12")) - total_source_calls)
        return people_search_service.search(user_id=user_id, run_id=run_id, request=request, source_call_budget=remaining).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=safe_provider_message("People search")).model_dump()


@tool
def get_person_details(
    candidate_id: str,
    people_candidates: Annotated[list[dict[str, Any]], InjectedState("people_candidates")],
    include_private_contact: bool = False,
    offset: int = 0,
    limit: int = 4000,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    intent: Annotated[str, InjectedState("intent")] = "",
) -> dict:
    """Read a bounded segment of a selected person's normalized record and user-owned public evidence."""
    candidate = next((item for item in people_candidates if item.get("candidate_id") == candidate_id), None)
    if candidate is None:
        return ToolExecutionResult(ok=False, error_type="NotFound", error_message="People candidate is not loaded.").model_dump(mode="json")
    try:
        data, evidence_ids = candidate_detail_page(user_id=user_id, run_id=run_id, candidate=candidate, offset=offset, limit=limit)
        if include_private_contact:
            if str(intent) not in {CareerIntent.OUTREACH.value, str(CareerIntent.OUTREACH)}:
                raise ValueError("Private contact is available only in the outreach workflow.")
            reference = candidate.get("private_contact_reference")
            if reference:
                connection = profile_repository.get_connection(user_id, reference)
                if connection.get("user_provided_email"):
                    data["private_contact_channels"] = [{"type": "email", "value": connection["user_provided_email"], "provenance": "user_provided_private", "visibility": "private_to_user", "evidence_id": None}]
        return ToolExecutionResult(ok=True, data=data, evidence_ids=evidence_ids).model_dump(mode="json")
    except Exception:
        return ToolExecutionResult(ok=False, error_type="DetailUnavailable", error_message="People detail is unavailable for this user and run.").model_dump(mode="json")
