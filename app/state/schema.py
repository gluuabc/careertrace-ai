from typing import Any, TypedDict

from pydantic import BaseModel, Field, field_validator


class ProjectFact(BaseModel):
    """A project normalized for SQL persistence and editing."""

    title: str = ""
    description: str = ""

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class ExperienceFact(BaseModel):
    """A work, research, teaching, or extracurricular experience."""

    organization: str = ""
    role: str = ""
    description: str = ""

    @field_validator("organization", "role", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class ProfileFacts(BaseModel):
    """Structured facts extracted from a resume."""

    name: str | None = None
    email: str | None = None
    education: list[str] = Field(default_factory=list)
    school: str | None = None
    major: str | None = None
    graduation_year: int | None = None
    skills: list[str] = Field(default_factory=list)
    courses: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    projects: list[ProjectFact] = Field(default_factory=list)
    experience: list[ExperienceFact] = Field(default_factory=list)
    career_goal: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    remote_preference: str | None = None

    @field_validator(
        "name",
        "email",
        "school",
        "major",
        "career_goal",
        "work_authorization",
        "remote_preference",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator(
        "education",
        "skills",
        "courses",
        "achievements",
        "certifications",
        "target_roles",
        "preferred_locations",
        "employment_types",
        mode="before",
    )
    @classmethod
    def strip_text_lists(cls, value: Any) -> list[str]:
        return [
            cleaned
            for item in (value or [])
            if (cleaned := str(item).strip())
        ]


class CareerProfile(BaseModel):
    """Initial guidance generated from a confirmed profile."""

    strengths: list[str] = Field(default_factory=list)
    possible_roles: list[str] = Field(default_factory=list)
    recommended_next_skills: list[str] = Field(default_factory=list)


class ProfileState(TypedDict, total=False):
    """Shared state for the controlled profile-onboarding workflow."""

    resume_path: str
    documents: list[dict[str, Any]]
    stored_documents: list[dict[str, Any]]
    document_texts: list[dict[str, str]]
    document_ids: list[str]
    original_filename: str
    content_type: str
    document_type: str
    document_id: str
    s3_key: str
    resume_text: str
    extracted_profile: dict[str, Any]
    existing_profile: dict[str, Any]
    missing_fields: list[str]
    validation_errors: list[str]
    profile_updates: dict[str, Any]
    confirmation_attempted: bool
    confirmed: bool
    user_id: str
    saved_profile: dict[str, Any]
    career_profile: dict[str, Any]
    analysis_id: str
