from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, session_scope
from app.database.models import (
    CareerAnalysis,
    CareerPreference,
    Document,
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

    def get_user(self, user_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Unknown user_id: {user_id}")
            return self._user_dict(user)

    def get_demo_user(self, demo_user_id: str) -> dict[str, Any]:
        """Return only the explicitly marked fixed demo account."""

        with session_scope(self.session_factory) as session:
            user = session.get(User, demo_user_id)
            if user is None or not user.is_demo:
                raise ValueError("The judge demo account is not available.")
            return self._user_dict(user)

    def reset_demo_account(
        self,
        *,
        demo_user_id: str,
        name: str,
        profile_data: dict[str, Any],
        analysis_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically replace the fixed demo account with synthetic seed data."""

        normalized = self._normalize_profile(profile_data)
        self._validate_required_profile(normalized)
        with session_scope(self.session_factory) as session:
            user = session.get(User, demo_user_id)
            if user is not None and not user.is_demo:
                raise ValueError("The fixed demo user_id belongs to a real account.")
            if user is None:
                user = User(
                    user_id=demo_user_id,
                    name=name,
                    email=None,
                    google_id=None,
                    profile_image=None,
                    is_demo=True,
                )
                session.add(user)
                session.flush()

            user.name = name
            user.email = None
            user.google_id = None
            user.profile_image = None
            user.is_demo = True

            if user.profile is None:
                user.profile = Profile()
            user.profile.education = normalized["education"]
            user.profile.school = normalized["school"]
            user.profile.major = normalized["major"]
            user.profile.graduation_year = normalized["graduation_year"]
            user.profile.career_goal = normalized["career_goal"]
            user.profile.version = 1

            if user.preferences is None:
                user.preferences = CareerPreference()
            user.preferences.target_roles = normalized["target_roles"]
            user.preferences.preferred_locations = normalized[
                "preferred_locations"
            ]
            user.preferences.employment_types = normalized["employment_types"]
            user.preferences.work_authorization = normalized[
                "work_authorization"
            ]
            user.preferences.remote_preference = normalized["remote_preference"]

            user.skills.clear()
            user.projects.clear()
            user.experience.clear()
            user.analyses.clear()
            user.documents.clear()
            session.flush()

            user.skills.extend(
                Skill(skill_name=skill) for skill in normalized["skills"]
            )
            user.projects.extend(
                Project(
                    title=self._project_title(project),
                    description=self._project_description(project),
                )
                for project in normalized["projects"]
            )
            user.experience.extend(
                Experience(
                    organization=self._item_value(item, "organization"),
                    role=self._item_value(item, "role"),
                    description=self._item_value(item, "description"),
                )
                for item in normalized["experience"]
            )
            user.analyses.append(
                CareerAnalysis(
                    strengths=list(analysis_data.get("strengths") or []),
                    possible_roles=list(analysis_data.get("possible_roles") or []),
                    recommended_next_skills=list(
                        analysis_data.get("recommended_next_skills") or []
                    ),
                    profile_version_used=1,
                )
            )
            session.flush()
            return self._user_dict(user)

    def get_or_create_user(
        self, name: str, email: str | None = None
    ) -> dict[str, Any]:
        clean_email = email.strip() if email and email.strip() else None
        with session_scope(self.session_factory) as session:
            user = None
            if clean_email:
                user = session.scalar(
                    select(User).where(func.lower(User.email) == clean_email)
                )
            if user is None:
                user = User(name=name.strip() or "CareerTrace User", email=clean_email)
                session.add(user)
                session.flush()
            elif name.strip() and user.name != name.strip():
                user.name = name.strip()
            return self._user_dict(user)

    def get_or_create_google_user(
        self,
        *,
        google_id: str,
        email: str,
        name: str,
        profile_image: str | None = None,
    ) -> dict[str, Any]:
        """Map a validated Google identity to the permanent UUID user ID."""

        clean_google_id = google_id.strip()
        clean_email = email.strip().casefold()
        if not clean_google_id or not clean_email:
            raise ValueError("A Google subject and verified email are required.")

        with session_scope(self.session_factory) as session:
            user = session.scalar(
                select(User).where(User.google_id == clean_google_id)
            )
            if user is None:
                user = session.scalar(select(User).where(User.email == clean_email))
                if user is not None and user.google_id not in {
                    None,
                    clean_google_id,
                }:
                    raise ValueError(
                        "This email is already linked to another Google identity."
                    )
                if user is None:
                    user = User(
                        google_id=clean_google_id,
                        email=clean_email,
                        name=name.strip() or clean_email,
                        profile_image=self._clean_optional(profile_image),
                    )
                    session.add(user)
                    session.flush()
                else:
                    user.google_id = clean_google_id

            user.email = clean_email
            user.name = name.strip() or user.name
            if self._clean_optional(profile_image):
                user.profile_image = self._clean_optional(profile_image)
            session.flush()
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
        """Replace changed facts transactionally without creating false versions."""

        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Unknown user_id: {user_id}")

            normalized = self._normalize_profile(profile_data)
            self._validate_required_profile(normalized)
            if user.google_id:
                # Google identity claims, not editable profile forms, own these
                # account-level fields.
                normalized["name"] = user.name
                normalized["email"] = user.email

            profile = user.profile
            if profile is not None:
                current = self._comparable_profile(self._profile_dict(user))
                if current == self._comparable_profile(normalized):
                    result = self._profile_dict(user)
                    result["profile_changed"] = False
                    return result

            user.name = normalized["name"] or user.name
            user.email = normalized["email"]

            if profile is None:
                profile = Profile(user=user)
                session.add(profile)
            else:
                profile.version += 1

            profile.education = normalized["education"]
            profile.school = normalized["school"]
            profile.major = normalized["major"]
            profile.graduation_year = normalized["graduation_year"]
            profile.career_goal = normalized["career_goal"]

            preferences = user.preferences
            if preferences is None:
                preferences = CareerPreference(user=user)
                session.add(preferences)
            preferences.target_roles = normalized["target_roles"]
            preferences.preferred_locations = normalized["preferred_locations"]
            preferences.employment_types = normalized["employment_types"]
            preferences.work_authorization = normalized["work_authorization"]
            preferences.remote_preference = normalized["remote_preference"]

            user.skills.clear()
            user.projects.clear()
            user.experience.clear()
            # Flush removals before inserting replacements so unique constraints
            # are respected consistently by SQLite and CockroachDB.
            session.flush()

            user.skills.extend(
                Skill(skill_name=skill)
                for skill in normalized["skills"]
            )

            user.projects.extend(
                Project(
                    title=self._project_title(project),
                    description=self._project_description(project),
                )
                for project in normalized["projects"]
                if self._project_title(project) or self._project_description(project)
            )

            user.experience.extend(
                Experience(
                    organization=self._item_value(item, "organization"),
                    role=self._item_value(item, "role"),
                    description=self._item_value(item, "description"),
                )
                for item in normalized["experience"]
                if any(
                    self._item_value(item, key)
                    for key in ("organization", "role", "description")
                )
            )

            session.flush()
            result = self._profile_dict(user)
            result["profile_changed"] = True
            return result

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
                profile_version_used=user.profile.version,
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

    def create_document(
        self,
        *,
        document_id: str,
        user_id: str,
        filename: str,
        s3_key: str,
        document_type: str,
        content_type: str,
        size_bytes: int,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            if session.get(User, user_id) is None:
                raise ValueError(f"Unknown user_id: {user_id}")
            document = Document(
                document_id=document_id,
                user_id=user_id,
                filename=filename,
                s3_key=s3_key,
                document_type=document_type,
                content_type=content_type,
                size_bytes=size_bytes,
            )
            session.add(document)
            session.flush()
            return self._document_dict(document)

    def list_documents(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            documents = session.scalars(
                select(Document)
                .where(Document.user_id == user_id)
                .order_by(Document.uploaded_at.desc())
            ).all()
            return [self._document_dict(document) for document in documents]

    def get_document(self, user_id: str, document_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            document = session.scalar(
                select(Document).where(
                    Document.document_id == document_id,
                    Document.user_id == user_id,
                )
            )
            if document is None:
                raise ValueError("Document was not found for this user.")
            return self._document_dict(document)

    def delete_document(self, user_id: str, document_id: str) -> None:
        with session_scope(self.session_factory) as session:
            document = session.scalar(
                select(Document).where(
                    Document.document_id == document_id,
                    Document.user_id == user_id,
                )
            )
            if document is None:
                raise ValueError("Document was not found for this user.")
            session.delete(document)

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

    @classmethod
    def _normalize_profile(cls, data: dict[str, Any]) -> dict[str, Any]:
        graduation_year = data.get("graduation_year")
        if graduation_year not in (None, ""):
            graduation_year = int(graduation_year)
        return {
            "name": cls._clean_optional(data.get("name")),
            "email": cls._clean_optional(data.get("email")),
            "education": cls._deduplicate(data.get("education") or []),
            "school": cls._clean_optional(data.get("school")),
            "major": cls._clean_optional(data.get("major")),
            "graduation_year": graduation_year,
            "career_goal": cls._clean_optional(data.get("career_goal")),
            "skills": cls._deduplicate(data.get("skills") or []),
            "projects": [
                {
                    "title": cls._project_title(item),
                    "description": cls._project_description(item),
                }
                for item in data.get("projects") or []
                if cls._project_title(item) or cls._project_description(item)
            ],
            "experience": [
                {
                    "organization": cls._item_value(item, "organization"),
                    "role": cls._item_value(item, "role"),
                    "description": cls._item_value(item, "description"),
                }
                for item in data.get("experience") or []
                if any(
                    cls._item_value(item, key)
                    for key in ("organization", "role", "description")
                )
            ],
            "target_roles": cls._deduplicate(data.get("target_roles") or []),
            "preferred_locations": cls._deduplicate(
                data.get("preferred_locations") or []
            ),
            "employment_types": cls._deduplicate(
                data.get("employment_types") or []
            ),
            "work_authorization": cls._clean_optional(
                data.get("work_authorization")
            ),
            "remote_preference": cls._clean_optional(
                data.get("remote_preference")
            ),
        }

    @staticmethod
    def _validate_required_profile(profile: dict[str, Any]) -> None:
        required = ("school", "major", "graduation_year", "skills", "experience")
        missing = [field for field in required if not profile.get(field)]
        if missing:
            raise ValueError(
                f"Required profile fields are missing: {', '.join(missing)}"
            )

    @staticmethod
    def _comparable_profile(profile: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "name",
            "email",
            "education",
            "school",
            "major",
            "graduation_year",
            "career_goal",
            "skills",
            "projects",
            "experience",
            "target_roles",
            "preferred_locations",
            "employment_types",
            "work_authorization",
            "remote_preference",
        )
        return {key: profile.get(key) for key in keys}

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
            "google_id": user.google_id,
            "name": user.name,
            "email": user.email,
            "profile_image": user.profile_image,
            "is_demo": user.is_demo,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
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
        current_version = analysis.user.profile.version if analysis.user.profile else 0
        return {
            "analysis_id": analysis.analysis_id,
            "user_id": analysis.user_id,
            "strengths": list(analysis.strengths or []),
            "possible_roles": list(analysis.possible_roles or []),
            "recommended_next_skills": list(
                analysis.recommended_next_skills or []
            ),
            "profile_version_used": analysis.profile_version_used,
            "is_stale": analysis.profile_version_used < current_version,
            "generated_at": analysis.generated_at.isoformat(),
        }

    @staticmethod
    def _document_dict(document: Document) -> dict[str, Any]:
        return {
            "document_id": document.document_id,
            "user_id": document.user_id,
            "filename": document.filename,
            "s3_key": document.s3_key,
            "document_type": document.document_type,
            "content_type": document.content_type,
            "size_bytes": document.size_bytes,
            "uploaded_at": document.uploaded_at.isoformat(),
        }


profile_repository = ProfileRepository()
