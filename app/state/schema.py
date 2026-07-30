from typing import Any, TypedDict

from pydantic import BaseModel, Field


class ProfileFacts(BaseModel):
    """Structured facts extracted from a resume."""

    education: list[str] = Field(default_factory=list)
    school: str | None = None
    major: str | None = None
    graduation_year: int | None = None
    skills: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)


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
    confirmed: bool
    saved_profile: dict[str, Any]
    career_profile: dict[str, Any]
