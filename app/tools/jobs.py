from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.job_search import job_search_service
from app.state.agent_schema import JobSearchRequest, ToolExecutionResult


@tool
def search_jobs(
    request: JobSearchRequest,
    user_id: Annotated[str, InjectedState("user_id")],
    run_id: Annotated[str, InjectedState("run_id")],
    total_source_calls: Annotated[int, InjectedState("total_source_calls")],
) -> dict:
    """Search permitted official public job sources. Use for an explicit job-search action after requirements are known. Missing fields remain unknown; hard constraints are never relaxed. This performs public GET requests and stores evidence, but never applies to a job."""
    try:
        import os

        remaining = max(
            0,
            int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12")) - total_source_calls,
        )
        return job_search_service.search(
            user_id=user_id,
            run_id=run_id,
            request=request,
            source_call_budget=remaining,
        ).model_dump(mode="json")
    except Exception as error:
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=str(error)[:500]).model_dump()


@tool
def get_job_details(
    candidate_id: str,
    job_candidates: Annotated[list[dict[str, Any]], InjectedState("job_candidates")],
) -> dict:
    """Return a previously loaded job candidate by ID. Do not use it to search or invent missing details; it has no external side effect."""
    candidate = next((item for item in job_candidates if item.get("candidate_id") == candidate_id), None)
    return ToolExecutionResult(ok=candidate is not None, data=candidate, error_type=None if candidate else "NotFound", error_message=None if candidate else "Job candidate is not loaded.").model_dump(mode="json")
