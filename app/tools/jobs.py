from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.job_search import job_search_service
from app.services.errors import safe_provider_message
from app.state.agent_schema import JobSearchRequest, ToolExecutionResult
from app.tools.detail import candidate_detail_page


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
        return ToolExecutionResult(ok=False, error_type=type(error).__name__, error_message=safe_provider_message("Job search")).model_dump()


@tool
def get_job_details(
    candidate_id: str,
    job_candidates: Annotated[list[dict[str, Any]], InjectedState("job_candidates")],
    offset: int = 0,
    limit: int = 4000,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
) -> dict:
    """Read a bounded segment of a selected job's complete normalized record and user-owned evidence."""
    candidate = next((item for item in job_candidates if item.get("candidate_id") == candidate_id), None)
    if candidate is None:
        return ToolExecutionResult(ok=False, error_type="NotFound", error_message="Job candidate is not loaded.").model_dump(mode="json")
    try:
        data, evidence_ids = candidate_detail_page(user_id=user_id, run_id=run_id, candidate=candidate, offset=offset, limit=limit)
        return ToolExecutionResult(ok=True, data=data, evidence_ids=evidence_ids).model_dump(mode="json")
    except Exception:
        return ToolExecutionResult(ok=False, error_type="DetailUnavailable", error_message="Job detail is unavailable for this user and run.").model_dump(mode="json")
