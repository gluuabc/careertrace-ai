from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar, TypedDict
from uuid import uuid4
import os

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_env_int(name: str, default: int, maximum: int) -> int:
    return max(1, min(int(os.getenv(name, str(default))), maximum))


class CareerIntent(StrEnum):
    CONCISE_GUIDANCE = "concise_guidance"
    ACTION_PLAN = "action_plan"
    JOB_SEARCH = "job_search"
    PEOPLE_SEARCH = "people_search"
    RESUME_REVISION = "resume_revision"
    OUTREACH = "outreach"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"


class MemorySignal(BaseModel):
    type: Literal[
        "profile.school",
        "profile.major",
        "profile.graduation_year",
        "profile.skills",
        "profile.projects",
        "profile.experience",
        "profile.work_authorization",
        "memory.preference",
        "memory.goal",
        "memory.constraint",
        "memory.event",
    ]
    operation_hint: Literal["add", "replace", "remove"] = "add"
    value_hint: list[str] = Field(default_factory=list)

    @field_validator("value_hint")
    @classmethod
    def normalize_value_hint(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


class IntentDecision(BaseModel):
    intent: CareerIntent
    goal: str
    needs_user_input: bool = False
    clarification_question: str | None = None
    memory_worthy: bool = False
    memory_signals: list[MemorySignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def align_memory_worthy(self):
        self.memory_worthy = bool(self.memory_signals)
        return self


class AgentTodoItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    status: Literal["pending", "in_progress", "blocked", "completed", "cancelled"] = "pending"
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


class RequirementState(StrEnum):
    MATCH = "match"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class SourceStatus(StrEnum):
    OFFICIAL_SOURCE = "official_source"
    VERIFIED_PUBLIC_SOURCE = "verified_public_source"
    USER_PROVIDED = "user_provided"
    DEMO_SNAPSHOT = "demo_snapshot"
    UNVERIFIED_PUBLIC_SOURCE = "unverified_public_source"


class RequirementStatus(StrEnum):
    MATCHES = "matches"
    REQUIREMENTS_NOT_FULLY_VERIFIED = "requirements_not_fully_verified"
    CONFLICT = "conflict"


class PeopleVerificationStatus(StrEnum):
    VERIFIED_PUBLIC = "verified_public"
    USER_PROVIDED_UNVERIFIED = "user_provided_unverified"
    INSUFFICIENT_PUBLIC_EVIDENCE = "insufficient_public_evidence"


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
    profile_skills: list[str] = Field(default_factory=list)
    desired_job_skills: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    salary_preference: str | None = None
    hard_preference_fields: list[str] = Field(default_factory=list)
    requested_count: int = Field(
        default_factory=lambda: _bounded_env_int("JOB_SEARCH_DEFAULT_N", 5, 10),
        ge=1,
        le=10,
    )
    max_results: int = Field(
        default_factory=lambda: _bounded_env_int("JOB_SEARCH_MAX_RESULTS", 10, 10),
        ge=1,
        le=10,
    )
    cursor: str | None = None
    page_size: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_skill_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy = list(migrated.pop("required_skills", []) or []) + list(
            migrated.pop("preferred_skills", []) or []
        )
        migrated["desired_job_skills"] = list(
            dict.fromkeys([*(migrated.get("desired_job_skills") or []), *legacy])
        )
        for field in ("requested_count", "max_results", "page_size"):
            if field in migrated:
                migrated[field] = min(max(int(migrated[field]), 1), 10)
        return migrated

    @field_validator("hard_preference_fields")
    @classmethod
    def validate_hard_preference_fields(cls, values: list[str]) -> list[str]:
        allowed = {"industries", "salary_preference"}
        normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        unknown = set(normalized) - allowed
        if unknown:
            raise ValueError(f"Unsupported hard preference fields: {sorted(unknown)}")
        return normalized


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
    first_seen_iteration: int = 1
    last_seen_iteration: int = 1
    source_keys: list[str] = Field(default_factory=list)
    hard_requirement_states: dict[str, RequirementState] = Field(default_factory=dict)
    job_required_skills: list[str] = Field(default_factory=list)
    job_preferred_skills: list[str] = Field(default_factory=list)
    ranking_components: dict[str, Any] = Field(default_factory=dict)
    source_status: SourceStatus = SourceStatus.UNVERIFIED_PUBLIC_SOURCE
    requirement_status: RequirementStatus = RequirementStatus.REQUIREMENTS_NOT_FULLY_VERIFIED
    is_demo_sample: bool = False
    snapshot_date: str | None = None
    source_verified_at_snapshot: bool | None = None
    verification_status: Literal["verified", "requirements_not_fully_verified", "conflict"] = "requirements_not_fully_verified"


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
    requested_count: int = Field(default=5, ge=1, le=10)
    max_results: int = Field(default=10, ge=1, le=10)
    cursor: str | None = None
    page_size: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def clamp_legacy_result_limits(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for field in ("requested_count", "max_results", "page_size"):
            if field in migrated:
                migrated[field] = min(max(int(migrated[field]), 1), 10)
        return migrated


class ContactChannel(BaseModel):
    type: Literal["email", "website", "profile", "other"]
    value: str
    provenance: Literal["public_verified", "user_provided_private"]
    visibility: Literal["public", "private_to_user"]
    evidence_id: str | None = None


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
    retrieved_at: datetime = Field(default_factory=utc_now)
    evidence_ids: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    contact_channels: list[ContactChannel] = Field(default_factory=list)
    private_contact_reference: str | None = None
    first_seen_iteration: int = 1
    last_seen_iteration: int = 1
    source_keys: list[str] = Field(default_factory=list)
    verification_status: PeopleVerificationStatus = PeopleVerificationStatus.INSUFFICIENT_PUBLIC_EVIDENCE
    identity_confidence: Literal["verified", "unverified", "unknown"] = "unknown"
    source_trust: dict[str, Any] = Field(default_factory=dict)
    ranking_components: dict[str, Any] = Field(default_factory=dict)
    source_status: SourceStatus = SourceStatus.UNVERIFIED_PUBLIC_SOURCE
    is_demo_sample: bool = False
    snapshot_date: str | None = None
    source_verified_at_snapshot: bool | None = None


class PeopleSearchSufficiency(BaseModel):
    requested_count: int
    verified_count: int
    unverified_count: int
    new_candidates_this_iteration: int
    remaining_source_budget: int
    has_more_sources: bool
    has_more_pages: bool
    can_refine: bool
    sufficient: bool
    stop_reason: str | None = None


SearchItem = TypeVar("SearchItem")


class SearchPage(BaseModel, Generic[SearchItem]):
    """Bounded page exposed to the model; full records stay in evidence storage."""

    items: list[SearchItem] = Field(default_factory=list)
    returned_count: int = 0
    total_count: int | None = None
    total_count_is_estimate: bool = True
    page_size: int = Field(default=10, ge=1, le=20)
    cursor: str | None = None
    next_cursor: str | None = None
    has_more: bool = False
    truncated: bool = False
    source_coverage: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceProgress(BaseModel):
    source_key: str
    provider: str
    company_or_domain: str | None = None
    query_hash: str
    visited: bool = False
    cursor: str | None = None
    next_cursor: str | None = None
    has_more: bool = False
    exhausted: bool = False
    call_count: int = 0
    first_iteration: int | None = None
    last_iteration: int | None = None
    last_success_at: datetime | None = None
    last_error_type: str | None = None


class SearchSessionState(BaseModel):
    search_session_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    user_id: str
    intent: Literal["job_search", "people_search"]
    normalized_request: dict[str, Any]
    requested_count: int
    iteration: int = 0
    max_iterations: int = Field(default=4, ge=1)
    source_call_budget: int = Field(default=12, ge=0)
    source_calls_used: int = Field(default=0, ge=0)
    remaining_source_budget: int = Field(default=12, ge=0)
    consecutive_no_progress: int = Field(default=0, ge=0)
    visited_sources: list[str] = Field(default_factory=list)
    provider_cursors: dict[str, str | None] = Field(default_factory=dict)
    seen_candidate_ids: list[str] = Field(default_factory=list)
    candidate_records: list[dict[str, Any]] = Field(default_factory=list)
    query_variants: list[str] = Field(default_factory=list)
    source_failures: dict[str, str] = Field(default_factory=dict)
    source_coverage: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "completed", "partial", "failed"] = "active"


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
    routing_source: str
    routing_diagnostics: dict[str, Any]
    memory_worthy: bool
    memory_signals: list[dict[str, Any]]
    effective_profile: dict[str, Any]
    user_message_id: str
    stop_after_tools: bool
    partial_result: bool
    personalization_references: dict[str, list[dict[str, Any]]]


def validate_todos(items: list[AgentTodoItem]) -> list[AgentTodoItem]:
    if sum(item.status == "in_progress" for item in items) > 1:
        raise ValueError("Only one TODO item may be in progress.")
    return items
