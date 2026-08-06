from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4
import os

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CareerIntent(StrEnum):
    CONCISE_GUIDANCE = "concise_guidance"
    ACTION_PLAN = "action_plan"
    JOB_SEARCH = "job_search"
    PEOPLE_SEARCH = "people_search"
    RESUME_REVISION = "resume_revision"
    OUTREACH = "outreach"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"


class IntentDecision(BaseModel):
    intent: CareerIntent
    goal: str
    needs_user_input: bool = False
    clarification_question: str | None = None


class AgentTodoItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("TODO content is required.")
        return value


class AgentStatus(BaseModel):
    goal: str = ""
    workflow_stage: str = "initializing"
    completed_steps: list[str] = Field(default_factory=list)
    current_step: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    verified_candidate_count: int = 0
    unverified_candidate_count: int = 0
    source_call_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_summary: str | None = None


class ToolExecutionResult(BaseModel):
    ok: bool
    data: Any = None
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_calls: int = 0


class JobSearchRequest(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    role_keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_preference: str | None = None
    employment_types: list[str] = Field(default_factory=list)
    student_level: str | None = None
    graduation_year: int | None = None
    work_authorization_requirement: str | None = None
    required_eligibility: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    salary_preference: str | None = None
    hard_preference_fields: list[str] = Field(default_factory=list)
    requested_count: int = Field(
        default_factory=lambda: int(os.getenv("JOB_SEARCH_DEFAULT_N", "5")),
        ge=1,
        le=20,
    )
    max_results: int = Field(
        default_factory=lambda: int(os.getenv("JOB_SEARCH_MAX_RESULTS", "20")),
        ge=1,
        le=20,
    )


class JobCandidate(BaseModel):
    candidate_id: str
    source_job_id: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    eligibility: str | None = None
    application_url: str | None = None
    source_name: str
    source_url: str
    retrieved_at: datetime = Field(default_factory=utc_now)
    posted_at: datetime | None = None
    deadline: datetime | None = None
    salary: str | None = None
    description_excerpt: str | None = None
    hard_constraints_met: bool = False
    failed_hard_constraints: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    eligibility_evidence_id: str | None = None
    deterministic_match_features: dict[str, Any] = Field(default_factory=dict)
    fit_score: float | None = None
    fit_explanation: str | None = None
    transferable_skills: list[str] = Field(default_factory=list)
    skill_gaps: list[str] = Field(default_factory=list)


class SearchSufficiency(BaseModel):
    requested_count: int
    verified_count: int
    unverified_count: int
    remaining_source_budget: int
    new_verified_candidates_this_iteration: int
    can_refine: bool
    stop_reason: str | None = None
    limiting_constraints: list[str] = Field(default_factory=list)
    suggested_relaxations: list[str] = Field(default_factory=list)


class PeopleSearchRequest(BaseModel):
    person_type: Literal["alumni", "professor", "recruiter"]
    organization: str | None = None
    school: str | None = None
    research_topics: list[str] = Field(default_factory=list)
    role_keywords: list[str] = Field(default_factory=list)
    requested_count: int = Field(default=5, ge=1, le=20)


class PeopleCandidate(BaseModel):
    candidate_id: str
    person_type: Literal["alumni", "professor", "recruiter"]
    name: str
    current_role: str | None = None
    organization: str | None = None
    education: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    research_topics: list[str] = Field(default_factory=list)
    public_profiles: list[str] = Field(default_factory=list)
    relevant_connection: list[str] = Field(default_factory=list)
    fit_explanation: str | None = None
    career_path_summary: str | None = None
    public_source_url: str
    public_contact: str | None = None
    contact_status: Literal["available", "unavailable"] = "unavailable"
    retrieved_at: datetime = Field(default_factory=utc_now)
    evidence_ids: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)


class ResumeRevisionChangeInput(BaseModel):
    section: str
    entry_identifier: str | None = None
    original_text: str | None = None
    proposed_text: str
    rationale: str
    profile_evidence_ids: list[str] = Field(default_factory=list)
    job_evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResumeRevisionDraftInput(BaseModel):
    source_profile_version_id: str
    source_document_ids: list[str] = Field(default_factory=list)
    target_job_ids: list[str] = Field(default_factory=list)
    template_id: str | None = None
    summary: str
    changes: list[ResumeRevisionChangeInput]


class OutreachDraftInput(BaseModel):
    outreach_type: Literal[
        "alumni_outreach",
        "professor_research_inquiry",
        "recruiter_introduction",
        "no_response_follow_up",
    ]
    recipient_candidate_id: str | None = None
    recipient_name: str
    recipient_role: str | None = None
    recipient_organization: str | None = None
    subject: str
    body: str
    relevant_connections: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    previous_draft_id: str | None = None


class CareerAgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    conversation_id: str
    run_id: str
    intent: CareerIntent
    current_goal: str
    current_request: str
    workflow_stage: str
    todo_items: list[dict[str, Any]]
    active_skill: str | None
    loaded_skills: dict[str, str]
    hard_constraints: dict[str, Any]
    soft_preferences: dict[str, Any]
    job_candidates: list[dict[str, Any]]
    people_candidates: list[dict[str, Any]]
    selected_job_ids: list[str]
    selected_people_ids: list[str]
    evidence_ids: list[str]
    tool_call_counts: dict[str, int]
    total_source_calls: int
    iteration: int
    consecutive_no_new_results: int
    warnings: list[str]
    current_error: str | None
    is_sufficient: bool
    needs_user_input: bool
    final_response: str
    status: dict[str, Any]
    runtime_context: str


def validate_todos(items: list[AgentTodoItem]) -> list[AgentTodoItem]:
    if sum(item.status == "in_progress" for item in items) > 1:
        raise ValueError("Only one TODO item may be in progress.")
    return items
