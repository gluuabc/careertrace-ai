from typing import Any, TypedDict

from pydantic import BaseModel, Field


class ProjectFact(BaseModel):
    """A project normalized for SQL persistence and editing."""

    title: str = ""
    description: str = ""


class ExperienceFact(BaseModel):
    """A work, research, teaching, or extracurricular experience."""

    organization: str = ""
    role: str = ""
    description: str = ""


class ProfileFacts(BaseModel):
    """Structured facts extracted from a resume."""

    name: str | None = None
    email: str | None = None
    education: list[str] = Field(default_factory=list)
    school: str | None = None
    major: str | None = None
    graduation_year: int | None = None
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectFact] = Field(default_factory=list)
    experience: list[ExperienceFact] = Field(default_factory=list)
    career_goal: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    remote_preference: str | None = None


class CareerProfile(BaseModel):
    """Initial guidance generated from a confirmed profile."""

    strengths: list[str] = Field(default_factory=list)
    possible_roles: list[str] = Field(default_factory=list)
    recommended_next_skills: list[str] = Field(default_factory=list)


class ProfileState(TypedDict, total=False):
    """Shared state for the controlled profile-onboarding workflow."""

    resume_path: str
    resume_text: str
    extracted_profile: dict[str, Any]
    missing_fields: list[str]
    validation_errors: list[str]
    profile_updates: dict[str, Any]
    confirmed: bool
    user_id: str
    saved_profile: dict[str, Any]
    career_profile: dict[str, Any]
    analysis_id: str
