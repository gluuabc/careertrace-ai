from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any
import threading

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, session_scope
from app.database.agent_repository import AgentRepositoryMixin
from app.database.models import (
    CareerAnalysis,
    CareerAnalysisVersion,
    CareerPreference,
    Conversation,
    Document,
    Experience,
    Memory,
    MemoryCandidate,
    Message,
    Profile,
    ProfileDocumentSource,
    ProfileVersion,
    Project,
    RetrievalDocument,
    Skill,
    StarredQAPair,
    User,
)


class ProfileRepository(AgentRepositoryMixin):
    """User-scoped SQL persistence boundary for CareerTrace."""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory
        self._source_budget_lock = threading.Lock()

    # ------------------------------------------------------------------ users
    def list_users(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            users = session.scalars(select(User).order_by(User.created_at)).all()
            return [self._user_dict(user) for user in users]

    def get_user(self, user_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            return self._user_dict(self._require_user(session, user_id))

    def create_demo_user(self) -> dict[str, Any]:
        """Create one ordinary isolated UUID user for a judge browser session."""

        with session_scope(self.session_factory) as session:
            user = User(
                name="Judge Demo",
                email=None,
                google_id=None,
                profile_image=None,
                is_demo=True,
            )
            session.add(user)
            session.flush()
            return self._user_dict(user)

    def get_demo_user(self, user_id: str) -> dict[str, Any]:
        """Resolve only the exact demo UUID already held by the current session."""

        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None or not user.is_demo:
                raise ValueError("The judge demo account is not available.")
            return self._user_dict(user)

    def get_or_create_user(
        self, name: str, email: str | None = None
    ) -> dict[str, Any]:
        clean_email = email.strip().casefold() if email and email.strip() else None
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
        """Map validated Google claims to one stable UUID-backed SQL user."""

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
                if user is not None and user.is_demo:
                    raise ValueError("A demo account cannot become a Google account.")
                if user is not None and user.google_id not in {None, clean_google_id}:
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

            if user.is_demo:
                raise ValueError("A demo account cannot become a Google account.")
            user.email = clean_email
            user.name = name.strip() or user.name
            if self._clean_optional(profile_image):
                user.profile_image = self._clean_optional(profile_image)
            session.flush()
            return self._user_dict(user)

    # ------------------------------------------------------- profile versions
    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if user is None or user.profile is None:
                return None
            return self._profile_dict(user)

    def upsert_profile(
        self,
        user_id: str,
        profile_data: dict[str, Any],
        document_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Create an immutable version only when confirmed facts changed."""

        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            normalized = self._normalize_profile(profile_data)
            self._validate_required_profile(normalized)
            if user.google_id:
                normalized["name"] = user.name
                normalized["email"] = user.email

            profile = user.profile
            if profile is not None and profile.current_version is not None:
                current_snapshot = dict(profile.current_version.snapshot_data)
                if self._comparable_profile(current_snapshot) == self._comparable_profile(
                    normalized
                ):
                    result = self._profile_dict(user)
                    result["profile_changed"] = False
                    return result

            user.name = normalized["name"] or user.name
            if not user.is_demo:
                user.email = normalized["email"]
            if profile is None:
                profile = Profile(user=user)
                session.add(profile)

            self._write_profile_scalars(user, profile, normalized)
            user.skills.clear()
            user.projects.clear()
            user.experience.clear()
            session.flush()
            self._write_profile_collections(user, normalized)
            next_number = (
                session.scalar(
                    select(func.max(ProfileVersion.version_number)).where(
                        ProfileVersion.user_id == user_id
                    )
                )
                or 0
            ) + 1
            version = ProfileVersion(
                user=user,
                version_number=next_number,
                snapshot_data=dict(normalized),
            )
            session.add(version)
            session.flush()

            source_ids = self._profile_source_ids(profile.current_version)
            source_ids.update(str(item) for item in (document_ids or []) if item)
            if source_ids:
                owned_documents = session.scalars(
                    select(Document).where(
                        Document.user_id == user_id,
                        Document.document_id.in_(source_ids),
                    )
                ).all()
                if {item.document_id for item in owned_documents} != source_ids:
                    raise ValueError("Every profile source must belong to this user.")
                version.document_sources.extend(
                    ProfileDocumentSource(document=document)
                    for document in owned_documents
                )

            profile.current_version = version
            profile.version = next_number
            session.flush()
            result = self._profile_dict(user)
            result["profile_changed"] = True
            return result

    def list_profile_versions(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            current_id = user.profile.current_version_id if user.profile else None
            versions = session.scalars(
                select(ProfileVersion)
                .where(ProfileVersion.user_id == user_id)
                .order_by(ProfileVersion.version_number.desc())
            ).all()
            return [
                self._profile_version_dict(version, version.version_id == current_id)
                for version in versions
            ]

    def rollback_profile(self, user_id: str, version_id: str) -> dict[str, Any]:
        """Move the current pointer without mutating or duplicating snapshots."""

        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            if user.profile is None:
                raise ValueError("This user does not have a profile.")
            version = session.scalar(
                select(ProfileVersion).where(
                    ProfileVersion.version_id == version_id,
                    ProfileVersion.user_id == user_id,
                )
            )
            if version is None:
                raise ValueError("Profile version was not found for this user.")
            user.profile.current_version = version
            session.flush()
            return self._profile_dict(user)

    # ------------------------------------------------------- analysis versions
    def save_analysis(
        self, user_id: str, analysis_data: dict[str, Any]
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            profile = user.profile
            if profile is None or profile.current_version is None:
                raise ValueError("A saved profile is required before career analysis.")

            normalized = self._normalize_analysis(analysis_data)
            next_number = (
                session.scalar(
                    select(func.max(CareerAnalysisVersion.version_number)).where(
                        CareerAnalysisVersion.user_id == user_id
                    )
                )
                or 0
            ) + 1
            version = CareerAnalysisVersion(
                user=user,
                profile_version=profile.current_version,
                version_number=next_number,
                analysis_data=normalized,
            )
            session.add(version)
            analysis = user.analysis
            if analysis is None:
                analysis = CareerAnalysis(user=user)
                session.add(analysis)
            analysis.current_version = version
            session.flush()
            return self._analysis_version_dict(
                version,
                current_profile_id=profile.current_version_id,
                is_current=True,
            )

    def get_latest_analysis(self, user_id: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            user = session.get(User, user_id)
            if (
                user is None
                or user.analysis is None
                or user.analysis.current_version is None
            ):
                return None
            profile_id = user.profile.current_version_id if user.profile else None
            return self._analysis_version_dict(
                user.analysis.current_version,
                current_profile_id=profile_id,
                is_current=True,
            )

    def list_analysis_versions(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            current_id = (
                user.analysis.current_version_id if user.analysis else None
            )
            profile_id = user.profile.current_version_id if user.profile else None
            versions = session.scalars(
                select(CareerAnalysisVersion)
                .where(CareerAnalysisVersion.user_id == user_id)
                .order_by(CareerAnalysisVersion.version_number.desc())
            ).all()
            return [
                self._analysis_version_dict(
                    version,
                    current_profile_id=profile_id,
                    is_current=version.analysis_version_id == current_id,
                )
                for version in versions
            ]

    def rollback_analysis(
        self, user_id: str, analysis_version_id: str
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            version = session.scalar(
                select(CareerAnalysisVersion).where(
                    CareerAnalysisVersion.analysis_version_id
                    == analysis_version_id,
                    CareerAnalysisVersion.user_id == user_id,
                )
            )
            if version is None:
                raise ValueError("Analysis version was not found for this user.")
            if user.analysis is None:
                user.analysis = CareerAnalysis()
            user.analysis.current_version = version
            session.flush()
            profile_id = user.profile.current_version_id if user.profile else None
            return self._analysis_version_dict(
                version,
                current_profile_id=profile_id,
                is_current=True,
            )

    # --------------------------------------------------------------- documents
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
            self._require_user(session, user_id)
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
            self._require_user(session, user_id)
            documents = session.scalars(
                select(Document)
                .where(Document.user_id == user_id)
                .order_by(Document.uploaded_at.desc())
            ).all()
            return [self._document_dict(document) for document in documents]

    def get_document(self, user_id: str, document_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            document = self._owned_document(session, user_id, document_id)
            return self._document_dict(document)

    def delete_document(self, user_id: str, document_id: str) -> None:
        with session_scope(self.session_factory) as session:
            document = self._owned_document(session, user_id, document_id)
            source_count = session.scalar(
                select(func.count(ProfileDocumentSource.document_id)).where(
                    ProfileDocumentSource.document_id == document_id
                )
            )
            if source_count:
                raise ValueError(
                    "A document used by profile history cannot be deleted."
                )
            session.execute(
                update(RetrievalDocument)
                .where(
                    RetrievalDocument.user_id == user_id,
                    RetrievalDocument.corpus_type == "uploaded_document_chunk",
                    RetrievalDocument.source_entity_id.like(f"{document_id}%"),
                )
                .values(active=False)
            )
            session.delete(document)

    # ------------------------------------------------------ flexible memories
    def create_memory_candidate(
        self,
        user_id: str,
        *,
        category: str,
        content: str,
        confidence: float | None,
        source: str,
    ) -> dict[str, Any]:
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1.")
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            candidate = MemoryCandidate(
                user=user,
                category=self._required_text(category, "category"),
                content=self._required_text(content, "content"),
                confidence=confidence,
                source=self._required_text(source, "source"),
            )
            session.add(candidate)
            session.flush()
            return self._memory_candidate_dict(candidate)

    def list_memory_candidates(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            candidates = session.scalars(
                select(MemoryCandidate)
                .where(MemoryCandidate.user_id == user_id)
                .order_by(MemoryCandidate.created_at.desc())
            ).all()
            return [self._memory_candidate_dict(item) for item in candidates]

    def review_memory_candidate(
        self, user_id: str, candidate_id: str, *, accept: bool
    ) -> dict[str, Any] | None:
        accepted_memory: dict[str, Any] | None = None
        with session_scope(self.session_factory) as session:
            candidate = session.scalar(
                select(MemoryCandidate).where(
                    MemoryCandidate.candidate_id == candidate_id,
                    MemoryCandidate.user_id == user_id,
                )
            )
            if candidate is None:
                raise ValueError("Memory candidate was not found for this user.")
            if candidate.status != "pending":
                raise ValueError("Memory candidate has already been reviewed.")
            candidate.status = "accepted" if accept else "rejected"
            candidate.reviewed_at = datetime.now(timezone.utc)
            if not accept:
                return None
            memory = Memory(
                user=candidate.user,
                category=candidate.category,
                content=candidate.content,
                confidence=candidate.confidence,
                source=candidate.source,
            )
            session.add(memory)
            session.flush()
            accepted_memory = self._memory_dict(memory)
        if accepted_memory is not None:
            # Approval is authoritative; retrieval indexing is additive and must not
            # make the approval transaction depend on an external embedding service.
            try:
                from app.database.retrieval_repository import RetrievalRepository
                from app.services.retrieval_corpus import RetrievalCorpusIndexer

                RetrievalCorpusIndexer(
                    RetrievalRepository(self.session_factory)
                ).index_memory(user_id=user_id, memory=accepted_memory)
            except Exception:
                pass
        return accepted_memory

    def list_memories(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            memories = session.scalars(
                select(Memory)
                .where(Memory.user_id == user_id)
                .order_by(Memory.created_at.desc())
            ).all()
            return [self._memory_dict(item) for item in memories]

    # ------------------------------------------------------------ conversation
    def create_conversation(self, user_id: str, title: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            conversation = Conversation(
                user=user,
                title=self._required_text(title, "title")[:200],
            )
            session.add(conversation)
            session.flush()
            return self._conversation_dict(conversation)

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            conversations = session.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
            ).all()
            return [self._conversation_dict(item) for item in conversations]

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(
                session, user_id, conversation_id
            )
            result = self._conversation_dict(conversation)
            result["messages"] = [
                self._message_dict(item)
                for item in sorted(
                    conversation.messages, key=lambda message: message.created_at
                )
            ]
            return result

    def add_message(
        self,
        user_id: str,
        conversation_id: str,
        role: str,
        content: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        clean_role = role.strip().lower()
        if clean_role not in {"user", "assistant"}:
            raise ValueError("Message role must be user or assistant.")
        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(
                session, user_id, conversation_id
            )
            reply_to = None
            if reply_to_message_id is not None:
                reply_to = session.scalar(
                    select(Message).where(
                        Message.message_id == reply_to_message_id,
                        Message.conversation_id == conversation_id,
                    )
                )
                if reply_to is None or reply_to.role != "user":
                    raise ValueError(
                        "Reply target must be a user message in this conversation."
                    )
                if clean_role != "assistant":
                    raise ValueError(
                        "Only an assistant message may reference a user question."
                    )
            message = Message(
                conversation=conversation,
                role=clean_role,
                content=self._required_text(content, "content"),
                reply_to=reply_to,
            )
            conversation.updated_at = datetime.now(timezone.utc)
            session.add(message)
            session.flush()
            return self._message_dict(message)

    # ------------------------------------------------------------- starred Q&A
    def star_qa_pair(
        self,
        user_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        *,
        preference_update_summary: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            conversation = self._owned_conversation(session, user_id, conversation_id)
            messages = session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.message_id.in_([user_message_id, assistant_message_id]),
                )
            ).all()
            by_id = {item.message_id: item for item in messages}
            question = by_id.get(user_message_id)
            answer = by_id.get(assistant_message_id)
            if question is None or question.role != "user":
                raise ValueError("The starred question was not found for this user.")
            if answer is None or answer.role != "assistant":
                raise ValueError("The starred answer was not found for this user.")
            if answer.reply_to_message_id != question.message_id:
                raise ValueError("The assistant message is not linked to this question.")
            existing = session.scalar(
                select(StarredQAPair).where(
                    StarredQAPair.user_id == user_id,
                    StarredQAPair.user_message_id == user_message_id,
                    StarredQAPair.assistant_message_id == assistant_message_id,
                )
            )
            if existing is not None:
                return self._starred_qa_dict(existing)
            item = StarredQAPair(
                user=user,
                conversation=conversation,
                user_message=question,
                assistant_message=answer,
                preference_update_summary=self._clean_optional(
                    preference_update_summary
                ),
            )
            session.add(item)
            session.flush()
            return self._starred_qa_dict(item)

    def unstar_qa_pair(self, user_id: str, starred_qa_id: str) -> None:
        with session_scope(self.session_factory) as session:
            item = session.scalar(
                select(StarredQAPair).where(
                    StarredQAPair.starred_qa_id == starred_qa_id,
                    StarredQAPair.user_id == user_id,
                )
            )
            if item is None:
                raise ValueError("Starred Q&A was not found for this user.")
            session.delete(item)

    def list_starred_qa_pairs(
        self, user_id: str, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            query = select(StarredQAPair).where(StarredQAPair.user_id == user_id)
            if conversation_id is not None:
                self._owned_conversation(session, user_id, conversation_id)
                query = query.where(StarredQAPair.conversation_id == conversation_id)
            items = session.scalars(
                query.order_by(StarredQAPair.created_at.desc())
            ).all()
            return [self._starred_qa_dict(item) for item in items]

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _require_user(session: Session, user_id: str) -> User:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError(f"Unknown user_id: {user_id}")
        return user

    @staticmethod
    def _owned_document(
        session: Session, user_id: str, document_id: str
    ) -> Document:
        document = session.scalar(
            select(Document).where(
                Document.document_id == document_id,
                Document.user_id == user_id,
            )
        )
        if document is None:
            raise ValueError("Document was not found for this user.")
        return document

    @staticmethod
    def _owned_conversation(
        session: Session, user_id: str, conversation_id: str
    ) -> Conversation:
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.conversation_id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if conversation is None:
            raise ValueError("Conversation was not found for this user.")
        return conversation

    @staticmethod
    def _clean_optional(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} is required.")
        return cleaned

    @staticmethod
    def _deduplicate(values: Iterable[Any]) -> list[str]:
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
            "courses": cls._deduplicate(data.get("courses") or []),
            "achievements": cls._deduplicate(data.get("achievements") or []),
            "certifications": cls._deduplicate(data.get("certifications") or []),
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

    @classmethod
    def _normalize_analysis(cls, data: dict[str, Any]) -> dict[str, list[str]]:
        return {
            "strengths": cls._deduplicate(data.get("strengths") or []),
            "possible_roles": cls._deduplicate(data.get("possible_roles") or []),
            "recommended_next_skills": cls._deduplicate(
                data.get("recommended_next_skills") or []
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
            "courses",
            "achievements",
            "certifications",
            "projects",
            "experience",
            "target_roles",
            "preferred_locations",
            "employment_types",
            "work_authorization",
            "remote_preference",
        )
        return {key: profile.get(key) for key in keys}

    @classmethod
    def _write_profile_scalars(
        cls, user: User, profile: Profile, normalized: dict[str, Any]
    ) -> None:
        profile.education = normalized["education"]
        profile.school = normalized["school"]
        profile.major = normalized["major"]
        profile.graduation_year = normalized["graduation_year"]
        profile.career_goal = normalized["career_goal"]

        if user.preferences is None:
            user.preferences = CareerPreference()
        user.preferences.target_roles = normalized["target_roles"]
        user.preferences.preferred_locations = normalized["preferred_locations"]
        user.preferences.employment_types = normalized["employment_types"]
        user.preferences.work_authorization = normalized["work_authorization"]
        user.preferences.remote_preference = normalized["remote_preference"]

    @staticmethod
    def _write_profile_collections(
        user: User, normalized: dict[str, Any]
    ) -> None:
        user.skills.extend(Skill(skill_name=item) for item in normalized["skills"])
        user.projects.extend(
            Project(title=item["title"], description=item["description"])
            for item in normalized["projects"]
        )
        user.experience.extend(
            Experience(
                organization=item["organization"],
                role=item["role"],
                description=item["description"],
            )
            for item in normalized["experience"]
        )

    @staticmethod
    def _profile_source_ids(version: ProfileVersion | None) -> set[str]:
        if version is None:
            return set()
        return {item.document_id for item in version.document_sources}

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
        if profile.current_version is not None:
            result = dict(profile.current_version.snapshot_data)
            result.update(
                {
                    "profile_version": profile.current_version.version_number,
                    "profile_version_id": profile.current_version.version_id,
                    "updated_at": profile.current_version.created_at.isoformat(),
                    "source_documents": [
                        cls._document_dict(item.document)
                        for item in profile.current_version.document_sources
                    ],
                }
            )
        else:
            result = cls._legacy_profile_dict(user)
        result["user_id"] = user.user_id
        if not user.is_demo:
            result["name"] = user.name
            result["email"] = user.email
        return result

    @staticmethod
    def _legacy_profile_dict(user: User) -> dict[str, Any]:
        profile = user.profile
        preferences = user.preferences
        return {
            "name": user.name,
            "email": user.email,
            "education": list(profile.education or []),
            "school": profile.school,
            "major": profile.major,
            "graduation_year": profile.graduation_year,
            "career_goal": profile.career_goal,
            "skills": [item.skill_name for item in user.skills],
            "courses": [],
            "achievements": [],
            "certifications": [],
            "projects": [
                {"title": item.title, "description": item.description}
                for item in user.projects
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
            "profile_version_id": None,
            "updated_at": profile.updated_at.isoformat(),
            "source_documents": [],
        }

    @classmethod
    def _profile_version_dict(
        cls, version: ProfileVersion, is_current: bool
    ) -> dict[str, Any]:
        return {
            "version_id": version.version_id,
            "user_id": version.user_id,
            "version_number": version.version_number,
            "snapshot_data": dict(version.snapshot_data),
            "created_at": version.created_at.isoformat(),
            "is_current": is_current,
            "source_documents": [
                cls._document_dict(item.document)
                for item in version.document_sources
            ],
        }

    @staticmethod
    def _analysis_version_dict(
        version: CareerAnalysisVersion,
        *,
        current_profile_id: str | None,
        is_current: bool,
    ) -> dict[str, Any]:
        data = dict(version.analysis_data)
        return {
            "analysis_id": version.analysis_version_id,
            "analysis_version_id": version.analysis_version_id,
            "analysis_version": version.version_number,
            "user_id": version.user_id,
            "strengths": list(data.get("strengths") or []),
            "possible_roles": list(data.get("possible_roles") or []),
            "recommended_next_skills": list(
                data.get("recommended_next_skills") or []
            ),
            "profile_version_id": version.profile_version_id,
            "profile_version_used": version.profile_version.version_number,
            "is_stale": version.profile_version_id != current_profile_id,
            "is_current": is_current,
            "generated_at": version.created_at.isoformat(),
        }

    @staticmethod
    def _document_dict(document: Document) -> dict[str, Any]:
        related = sorted(
            {item.profile_version.version_number for item in document.profile_sources}
        )
        return {
            "document_id": document.document_id,
            "user_id": document.user_id,
            "filename": document.filename,
            "s3_key": document.s3_key,
            "document_type": document.document_type,
            "content_type": document.content_type,
            "size_bytes": document.size_bytes,
            "uploaded_at": document.uploaded_at.isoformat(),
            "profile_versions": related,
        }

    @staticmethod
    def _memory_candidate_dict(item: MemoryCandidate) -> dict[str, Any]:
        return {
            "candidate_id": item.candidate_id,
            "user_id": item.user_id,
            "category": item.category,
            "content": item.content,
            "confidence": item.confidence,
            "source": item.source,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
            "reviewed_at": item.reviewed_at.isoformat()
            if item.reviewed_at
            else None,
        }

    @staticmethod
    def _memory_dict(item: Memory) -> dict[str, Any]:
        return {
            "memory_id": item.memory_id,
            "user_id": item.user_id,
            "category": item.category,
            "content": item.content,
            "confidence": item.confidence,
            "source": item.source,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _conversation_dict(item: Conversation) -> dict[str, Any]:
        return {
            "conversation_id": item.conversation_id,
            "user_id": item.user_id,
            "title": item.title,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _message_dict(item: Message) -> dict[str, Any]:
        return {
            "message_id": item.message_id,
            "conversation_id": item.conversation_id,
            "role": item.role,
            "content": item.content,
            "reply_to_message_id": item.reply_to_message_id,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _starred_qa_dict(item: StarredQAPair) -> dict[str, Any]:
        return {
            "starred_qa_id": item.starred_qa_id,
            "conversation_id": item.conversation_id,
            "conversation_title": item.conversation.title,
            "user_message_id": item.user_message_id,
            "assistant_message_id": item.assistant_message_id,
            "question": item.user_message.content,
            "answer": item.assistant_message.content,
            "preference_update_summary": item.preference_update_summary,
            "created_at": item.created_at.isoformat(),
        }


profile_repository = ProfileRepository()
