from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, session_scope
from app.database.models import (
    CareerAnalysis,
    CareerPreference,
    Experience,
    Profile,
    Project,
    Skill,
    User,
)


class ProfileRepository:
    """SQL persistence boundary used by graph nodes and the UI."""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory

    def list_users(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            users = session.scalars(select(User).order_by(User.created_at)).all()
            return [self._user_dict(user) for user in users]

    def get_or_create_user(
        self, name: str, email: str | None = None
    ) -> dict[str, Any]:
        clean_email = email.strip() if email and email.strip() else None
        with session_scope(self.session_factory) as session:
            user = None
            if clean_email:
                user = session.scalar(select(User).where(User.email == clean_email))
            if user is None:
                user = User(name=name.strip() or "CareerTrace User", email=clean_email)
                session.add(user)
                session.flush()
            elif name.strip() and user.name != name.strip():
                user.name = name.strip()
            return self._user_dict(user)

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None or user.profile is None:
                return None
            return self._profile_dict(user)

    def upsert_profile(
        self, user_id: str, profile_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace one confirmed profile bundle in a single transaction."""

        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Unknown user_id: {user_id}")

            if profile_data.get("name"):
                user.name = str(profile_data["name"]).strip()
            if "email" in profile_data:
                email = profile_data.get("email")
                user.email = str(email).strip() if email else None

            profile = user.profile
            if profile is None:
                profile = Profile(user=user)
                session.add(profile)
            else:
                profile.version += 1

            profile.education = list(profile_data.get("education") or [])
            profile.school = self._clean_optional(profile_data.get("school"))
            profile.major = self._clean_optional(profile_data.get("major"))
            profile.graduation_year = profile_data.get("graduation_year")
            profile.career_goal = self._clean_optional(
                profile_data.get("career_goal")
            )

            preferences = user.preferences
            if preferences is None:
                preferences = CareerPreference(user=user)
                session.add(preferences)
            preferences.target_roles = list(profile_data.get("target_roles") or [])
            preferences.preferred_locations = list(
                profile_data.get("preferred_locations") or []
            )
            preferences.employment_types = list(
                profile_data.get("employment_types") or []
            )
            preferences.work_authorization = self._clean_optional(
                profile_data.get("work_authorization")
            )
            preferences.remote_preference = self._clean_optional(
                profile_data.get("remote_preference")
            )

            user.skills.clear()
            user.projects.clear()
            user.experience.clear()
            # Flush removals before inserting replacements so unique constraints
            # are respected consistently by SQLite and CockroachDB.
            session.flush()

            user.skills.extend(
                Skill(skill_name=skill)
                for skill in self._deduplicate(profile_data.get("skills") or [])
            )

            user.projects.extend(
                Project(
                    title=self._project_title(project),
                    description=self._project_description(project),
                )
                for project in profile_data.get("projects") or []
                if self._project_title(project) or self._project_description(project)
            )

            user.experience.extend(
                Experience(
                    organization=self._item_value(item, "organization"),
                    role=self._item_value(item, "role"),
                    description=self._item_value(item, "description"),
                )
                for item in profile_data.get("experience") or []
                if any(
                    self._item_value(item, key)
                    for key in ("organization", "role", "description")
                )
            )

            session.flush()
            return self._profile_dict(user)

    def save_analysis(
        self, user_id: str, analysis_data: dict[str, Any]
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None or user.profile is None:
                raise ValueError("A saved profile is required before career analysis.")

            analysis = CareerAnalysis(
                user=user,
                strengths=list(analysis_data.get("strengths") or []),
                possible_roles=list(analysis_data.get("possible_roles") or []),
                recommended_next_skills=list(
                    analysis_data.get("recommended_next_skills") or []
                ),
                profile_version=user.profile.version,
            )
            session.add(analysis)
            session.flush()
            return self._analysis_dict(analysis)

    def get_latest_analysis(self, user_id: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            analysis = session.scalar(
                select(CareerAnalysis)
                .where(CareerAnalysis.user_id == user_id)
                .order_by(CareerAnalysis.generated_at.desc())
                .limit(1)
            )
            return self._analysis_dict(analysis) if analysis else None

    @staticmethod
    def _clean_optional(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _deduplicate(values: list[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value).strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result

    @staticmethod
    def _item_value(item: Any, key: str) -> str:
        if isinstance(item, dict):
            return str(item.get(key) or "").strip()
        return str(item).strip() if key == "description" else ""

    @classmethod
    def _project_title(cls, project: Any) -> str:
        if isinstance(project, dict):
            title = cls._item_value(project, "title")
            if title:
                return title
            description = cls._item_value(project, "description")
            return description[:100] or "Untitled project"
        text = str(project).strip()
        return text[:100] or "Untitled project"

    @classmethod
    def _project_description(cls, project: Any) -> str:
        if isinstance(project, dict):
            return cls._item_value(project, "description")
        return str(project).strip()

    @staticmethod
    def _user_dict(user: User) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        }

    @classmethod
    def _profile_dict(cls, user: User) -> dict[str, Any]:
        profile = user.profile
        if profile is None:
            raise ValueError("User does not have a profile.")
        preferences = user.preferences
        return {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "education": list(profile.education or []),
            "school": profile.school,
            "major": profile.major,
            "graduation_year": profile.graduation_year,
            "career_goal": profile.career_goal,
            "skills": [skill.skill_name for skill in user.skills],
            "projects": [
                {"title": project.title, "description": project.description}
                for project in user.projects
            ],
            "experience": [
                {
                    "organization": item.organization,
                    "role": item.role,
                    "description": item.description,
                }
                for item in user.experience
            ],
            "target_roles": list(preferences.target_roles or [])
            if preferences
            else [],
            "preferred_locations": list(preferences.preferred_locations or [])
            if preferences
            else [],
            "employment_types": list(preferences.employment_types or [])
            if preferences
            else [],
            "work_authorization": preferences.work_authorization
            if preferences
            else None,
            "remote_preference": preferences.remote_preference
            if preferences
            else None,
            "profile_version": profile.version,
            "updated_at": profile.updated_at.isoformat(),
        }

    @staticmethod
    def _analysis_dict(analysis: CareerAnalysis) -> dict[str, Any]:
        return {
            "analysis_id": analysis.analysis_id,
            "user_id": analysis.user_id,
            "strengths": list(analysis.strengths or []),
            "possible_roles": list(analysis.possible_roles or []),
            "recommended_next_skills": list(
                analysis.recommended_next_skills or []
            ),
            "profile_version": analysis.profile_version,
            "generated_at": analysis.generated_at.isoformat(),
        }


profile_repository = ProfileRepository()
