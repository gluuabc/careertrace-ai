from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.people_search import people_search_service
from app.state.agent_schema import PeopleSearchRequest, ToolExecutionResult


@tool
def search_people(
    request: PeopleSearchRequest,
    user_id: Annotated[str, InjectedState("user_id")],
    run_id: Annotated[str, InjectedState("run_id")],
) -> dict:
    """Search user-owned connections and permitted public sources for alumni, professors, or verified recruiters. Never scrape LinkedIn or infer private contact information. Public evidence is stored for returned external identities."""
    try:
        return people_search_service.search(user_id=user_id, run_id=run_id, request=request).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=str(error)[:500]).model_dump()


@tool
def get_person_details(
    candidate_id: str,
    people_candidates: Annotated[list[dict[str, Any]], InjectedState("people_candidates")],
) -> dict:
    """Return a previously loaded people candidate by ID. It does not search for or infer additional contact information."""
    candidate = next((item for item in people_candidates if item.get("candidate_id") == candidate_id), None)
    return ToolExecutionResult(ok=candidate is not None, data=candidate, error_type=None if candidate else "NotFound", error_message=None if candidate else "People candidate is not loaded.").model_dump(mode="json")
