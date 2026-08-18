from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any
import json
import threading

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, session_scope
from app.database.agent_repository import AgentRepositoryMixin
from app.database.models import (
    CareerAnalysis,
    CareerAnalysisVersion,
    CareerEvent,
    CareerPath,
    CareerPreference,
    Conversation,
    ConversationMemorySignal,
    ConversationMemoryState,
    Document,
    Experience,
    JudgeWorkspaceCredential,
    Memory,
    MemoryCandidate,
    MemoryExtractionRun,
    Message,
    Profile,
    ProfileDocumentSource,
    ProfileFieldRevision,
    ProfileRevisionChange,
    ProfileRevisionDraft,
    ProfileVersion,
    Project,
    RetrievalDocument,
    Skill,
    SemanticMemory,
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

    def create_judge_workspace(self, recovery_code_hash: str) -> dict[str, Any]:
        """Atomically create one isolated demo UUID and its hashed credential."""

        clean_hash = self._required_text(recovery_code_hash, "recovery code hash")
        if len(clean_hash) != 64:
            raise ValueError("Recovery code hash must be a SHA-256 digest.")
        with session_scope(self.session_factory) as session:
            user = User(
                name="Judge Demo",
                email=None,
                google_id=None,
                profile_image=None,
                is_demo=True,
            )
            user.judge_workspace_credentials.append(
                JudgeWorkspaceCredential(recovery_code_hash=clean_hash)
            )
            session.add(user)
            session.flush()
            return self._user_dict(user)

    def get_judge_workspace_by_recovery_hash(
        self, recovery_code_hash: str
    ) -> dict[str, Any]:
        """Resolve only an active recovery credential for an isolated demo user."""

        with session_scope(self.session_factory) as session:
            credential = session.scalar(
                select(JudgeWorkspaceCredential).where(
                    JudgeWorkspaceCredential.recovery_code_hash == recovery_code_hash,
                    JudgeWorkspaceCredential.revoked_at.is_(None),
                )
            )
            if credential is None or not credential.user.is_demo:
                raise ValueError("Judge workspace recovery failed.")
            return self._user_dict(credential.user)

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
        """Compatibility wrapper around the canonical field mutation boundary."""

        return self.apply_profile_field_changes(
            user_id,
            profile_data,
            source_type="document" if document_ids else "manual",
            document_ids=document_ids,
        )

    def apply_profile_field_changes(
        self,
        user_id: str,
        field_changes: dict[str, Any],
        *,
        source_type: str,
        source_conversation_id: str | None = None,
        source_message_ids: Iterable[str] | None = None,
        document_ids: Iterable[str] | None = None,
        operations: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Atomically apply explicit fields and record field-scoped audit history.

        Whole profile snapshots remain the current source of truth. Retrieval rows
        for the former current snapshot are deactivated in the same SQL
        transaction so external embedding work can never leave stale profile data
        marked current.
        """

        allowed_sources = {"manual", "conversation", "document", "history_restore"}
        if source_type not in allowed_sources:
            raise ValueError(f"Unsupported profile change source: {source_type}")
        unknown_fields = set(field_changes) - set(self._profile_field_keys())
        if unknown_fields:
            raise ValueError(
                "Unsupported profile fields: " + ", ".join(sorted(unknown_fields))
            )

        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            if source_conversation_id is not None:
                self._owned_conversation(session, user_id, source_conversation_id)

            profile = user.profile
            current_snapshot = (
                dict(profile.current_version.snapshot_data)
                if profile is not None and profile.current_version is not None
                else {}
            )
            composed = {**current_snapshot, **field_changes}
            normalized = self._normalize_profile(composed)
            self._validate_required_profile(normalized)
            if user.google_id:
                normalized["name"] = user.name
                normalized["email"] = user.email

            before = self._normalize_profile(current_snapshot) if current_snapshot else {}
            changed_fields = [
                key
                for key in self._profile_field_keys()
                if before.get(key) != normalized.get(key)
                and (current_snapshot or normalized.get(key) not in (None, "", []))
            ]
            if not changed_fields and profile is not None:
                result = self._profile_dict(user)
                result.update(
                    profile_changed=False,
                    field_revisions=[],
                    retrieval_index_status=(
                        profile.current_version.retrieval_index_status
                        if profile.current_version
                        else None
                    ),
                )
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
                retrieval_index_status="pending",
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
            revisions = []
            source_ids = [str(item) for item in (source_message_ids or []) if item]
            for field_key in changed_fields:
                revision = ProfileFieldRevision(
                    user=user,
                    field_key=field_key,
                    operation=(operations or {}).get(
                        field_key,
                        "set" if not current_snapshot else "replace",
                    ),
                    previous_value=before.get(field_key),
                    new_value=normalized.get(field_key),
                    source_type=source_type,
                    source_conversation_id=source_conversation_id,
                    source_message_ids=source_ids,
                    resulting_profile_version=version,
                )
                session.add(revision)
                revisions.append(revision)

            session.execute(
                update(RetrievalDocument)
                .where(
                    RetrievalDocument.user_id == user_id,
                    RetrievalDocument.corpus_type.in_(["resume", "project"]),
                    RetrievalDocument.active.is_(True),
                )
                .values(active=False)
            )
            session.flush()
            result = self._profile_dict(user)
            result.update(
                profile_changed=True,
                changed_fields=changed_fields,
                field_revisions=[self._profile_field_revision_dict(item) for item in revisions],
                retrieval_index_status="pending",
            )
            return result

    def set_profile_retrieval_index_status(
        self,
        user_id: str,
        profile_version_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in {"pending", "ready", "sparse_only", "failed"}:
            raise ValueError("Unsupported profile retrieval index status.")
        with session_scope(self.session_factory) as session:
            version = session.scalar(
                select(ProfileVersion).where(
                    ProfileVersion.version_id == profile_version_id,
                    ProfileVersion.user_id == user_id,
                )
            )
            if version is None:
                raise ValueError("Profile version was not found for this user.")
            version.retrieval_index_status = status
            version.retrieval_index_error = self._clean_optional(error)

    def list_profile_field_revisions(
        self, user_id: str, field_key: str | None = None
    ) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            query = select(ProfileFieldRevision).where(
                ProfileFieldRevision.user_id == user_id
            )
            if field_key is not None:
                query = query.where(ProfileFieldRevision.field_key == field_key)
            items = session.scalars(
                query.order_by(
                    ProfileFieldRevision.created_at.desc(),
                    ProfileFieldRevision.revision_id.desc(),
                )
            ).all()
            return [self._profile_field_revision_dict(item) for item in items]

    def list_profile_field_history(
        self, user_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Return distinct prior values per field, including legacy snapshots."""

        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            current = (
                dict(user.profile.current_version.snapshot_data)
                if user.profile is not None and user.profile.current_version is not None
                else {}
            )
            history = {key: [] for key in self._profile_field_keys()}
            seen = {
                key: {self._profile_value_identity(current.get(key))}
                for key in self._profile_field_keys()
            }

            revisions = session.scalars(
                select(ProfileFieldRevision)
                .where(ProfileFieldRevision.user_id == user_id)
                .order_by(
                    ProfileFieldRevision.created_at.desc(),
                    ProfileFieldRevision.revision_id.desc(),
                )
            ).all()
            for revision in revisions:
                if revision.previous_value in (None, "", []):
                    continue
                identity = self._profile_value_identity(revision.previous_value)
                if identity not in seen[revision.field_key]:
                    seen[revision.field_key].add(identity)
                    history[revision.field_key].append(
                        {
                            "value": revision.previous_value,
                            "created_at": revision.created_at.isoformat(),
                            "source_type": revision.source_type,
                        }
                    )

            versions = session.scalars(
                select(ProfileVersion)
                .where(ProfileVersion.user_id == user_id)
                .order_by(ProfileVersion.version_number.desc())
            ).all()
            for version in versions:
                for field_key in self._profile_field_keys():
                    value = version.snapshot_data.get(field_key)
                    if value in (None, "", []):
                        continue
                    identity = self._profile_value_identity(value)
                    if identity in seen[field_key]:
                        continue
                    seen[field_key].add(identity)
                    history[field_key].append(
                        {
                            "value": value,
                            "created_at": version.created_at.isoformat(),
                            "source_type": "legacy_snapshot",
                        }
                    )
            return history

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
        operation: str = "ADD",
        existing_memory_id: str | None = None,
        source_conversation_id: str | None = None,
        source_message_ids: list[str] | None = None,
        extraction_run_id: str | None = None,
        event_time: datetime | None = None,
        raw_temporal_expression: str | None = None,
        memory_kind: str = "legacy",
        existing_entity_id: str | None = None,
        semantic_group: str | None = None,
        topic_key: str | None = None,
        proposed_value: Any = None,
        event_status: str | None = None,
        evidence_text: str | None = None,
        evidence_start: int | None = None,
        evidence_end: int | None = None,
        proposal_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1.")
        clean_operation = operation.strip().upper()
        if clean_operation not in {"ADD", "UPDATE", "REVOKE", "NOOP", "CONFLICT"}:
            raise ValueError("Unsupported memory operation.")
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            if existing_memory_id:
                existing = session.scalar(
                    select(Memory).where(
                        Memory.memory_id == existing_memory_id,
                        Memory.user_id == user_id,
                    )
                )
                if existing is None:
                    raise ValueError("The referenced memory was not found for this user.")
            if source_conversation_id:
                self._owned_conversation(session, user_id, source_conversation_id)
            clean_message_ids = list(dict.fromkeys(source_message_ids or []))
            if clean_message_ids:
                if not source_conversation_id:
                    raise ValueError("Source messages require a source conversation.")
                owned_message_ids = set(
                    session.scalars(
                        select(Message.message_id).where(
                            Message.message_id.in_(clean_message_ids),
                            Message.conversation_id == source_conversation_id,
                        )
                    ).all()
                )
                if owned_message_ids != set(clean_message_ids):
                    raise ValueError("A source message was not found in this conversation.")
            if extraction_run_id:
                extraction_run = session.scalar(
                    select(MemoryExtractionRun).where(
                        MemoryExtractionRun.extraction_run_id == extraction_run_id,
                        MemoryExtractionRun.user_id == user_id,
                    )
                )
                if extraction_run is None or (
                    source_conversation_id
                    and extraction_run.conversation_id != source_conversation_id
                ):
                    raise ValueError("The extraction run was not found for this user and conversation.")
            candidate = MemoryCandidate(
                user=user,
                category=self._required_text(category, "category"),
                content=self._required_text(content, "content"),
                confidence=confidence,
                source=self._required_text(source, "source"),
                operation=clean_operation,
                existing_memory_id=existing_memory_id,
                source_conversation_id=source_conversation_id,
                source_message_ids=clean_message_ids,
                extraction_run_id=extraction_run_id,
                event_time=event_time,
                raw_temporal_expression=raw_temporal_expression,
                memory_kind=self._required_text(memory_kind, "memory kind"),
                existing_entity_id=existing_entity_id,
                semantic_group=semantic_group,
                topic_key=topic_key,
                proposed_value=proposed_value,
                event_status=event_status,
                evidence_text=evidence_text,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
                proposal_sources=list(dict.fromkeys(proposal_sources or [])),
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
        self,
        user_id: str,
        candidate_id: str,
        *,
        accept: bool,
        conflict_resolution: str | None = None,
    ) -> dict[str, Any] | None:
        preview = next(
            (item for item in self.list_memory_candidates(user_id) if item["candidate_id"] == candidate_id),
            None,
        )
        if preview and preview.get("memory_kind") in {"semantic", "episodic"}:
            return self._review_structured_memory_candidate(
                user_id, candidate_id, accept=accept,
                conflict_resolution=conflict_resolution,
            )
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
            resolution = conflict_resolution or ("use_new" if accept else "reject_new")
            if candidate.operation == "CONFLICT" and resolution not in {
                "keep_existing", "use_new", "keep_both", "reject_new"
            }:
                raise ValueError("Unsupported conflict resolution.")
            candidate.status = "accepted" if accept else "rejected"
            candidate.reviewed_at = datetime.now(timezone.utc)
            if not accept or resolution in {"keep_existing", "reject_new"}:
                candidate.status = "rejected"
                return None
            if candidate.memory_kind == "semantic":
                existing_semantic = session.scalar(select(SemanticMemory).where(
                    SemanticMemory.semantic_memory_id == candidate.existing_entity_id,
                    SemanticMemory.user_id == user_id,
                )) if candidate.existing_entity_id else None
                if candidate.operation == "REVOKE":
                    if existing_semantic is None:
                        raise ValueError("The semantic memory selected for revocation no longer exists.")
                    existing_semantic.active = False
                    existing_semantic.revoked_at = datetime.now(timezone.utc)
                    existing_semantic.retrieval_index_status = "inactive"
                    return self._semantic_memory_dict(existing_semantic)
                if candidate.operation in {"UPDATE", "CONFLICT"} and resolution == "use_new":
                    if existing_semantic is None:
                        raise ValueError("The semantic memory selected for update no longer exists.")
                    existing_semantic.active = False
                    existing_semantic.revoked_at = datetime.now(timezone.utc)
                    existing_semantic.retrieval_index_status = "inactive"
                item = SemanticMemory(
                    user_id=user_id, semantic_group=candidate.semantic_group or candidate.category,
                    topic_key=candidate.topic_key, value=candidate.proposed_value if candidate.proposed_value is not None else candidate.content,
                    source=candidate.source, source_conversation_id=candidate.source_conversation_id,
                    source_message_ids=list(candidate.source_message_ids or []), evidence_text=candidate.evidence_text,
                    active=True, retrieval_index_status="pending",
                    supersedes_semantic_memory_id=existing_semantic.semantic_memory_id if existing_semantic and resolution != "keep_both" else None,
                )
                session.add(item)
                session.flush()
                return self._semantic_memory_dict(item)
            if candidate.memory_kind == "episodic":
                item = CareerEvent(
                    user_id=user_id, content=candidate.content, event_status=candidate.event_status or "unknown",
                    event_time=candidate.event_time, raw_temporal_expression=candidate.raw_temporal_expression,
                    source=candidate.source, source_conversation_id=candidate.source_conversation_id,
                    source_message_ids=list(candidate.source_message_ids or []), evidence_text=candidate.evidence_text,
                    active=True, retrieval_index_status="pending",
                )
                session.add(item)
                session.flush()
                return self._career_event_dict(item)
            existing = (
                session.scalar(
                    select(Memory).where(
                        Memory.memory_id == candidate.existing_memory_id,
                        Memory.user_id == user_id,
                    )
                )
                if candidate.existing_memory_id
                else None
            )
            if candidate.operation == "REVOKE":
                if existing is None:
                    raise ValueError("The memory selected for revocation no longer exists.")
                existing.active = False
                existing.revoked_at = datetime.now(timezone.utc)
                existing.retrieval_index_status = "inactive"
                existing.retrieval_index_error = None
                session.execute(
                    update(RetrievalDocument)
                    .where(
                        RetrievalDocument.user_id == user_id,
                        RetrievalDocument.corpus_type == "approved_memory",
                        RetrievalDocument.source_entity_id.like(f"{existing.memory_id}%"),
                        RetrievalDocument.active.is_(True),
                    )
                    .values(active=False)
                )
                return self._memory_dict(existing)
            if candidate.operation == "UPDATE" or (
                candidate.operation == "CONFLICT" and resolution == "use_new"
            ):
                if existing is None:
                    raise ValueError("The memory selected for update no longer exists.")
                existing.active = False
                existing.revoked_at = datetime.now(timezone.utc)
                existing.retrieval_index_status = "inactive"
                existing.retrieval_index_error = None
                session.execute(
                    update(RetrievalDocument)
                    .where(
                        RetrievalDocument.user_id == user_id,
                        RetrievalDocument.corpus_type == "approved_memory",
                        RetrievalDocument.source_entity_id.like(f"{existing.memory_id}%"),
                        RetrievalDocument.active.is_(True),
                    )
                    .values(active=False)
                )
            memory = Memory(
                user=candidate.user,
                category=candidate.category,
                content=candidate.content,
                confidence=candidate.confidence,
                source=candidate.source,
                active=True,
                supersedes_memory_id=(
                    existing.memory_id
                    if existing is not None and resolution != "keep_both"
                    else None
                ),
                event_time=candidate.event_time,
                source_conversation_id=candidate.source_conversation_id,
                source_message_ids=list(candidate.source_message_ids or []),
                retrieval_index_status="pending",
            )
            session.add(memory)
            session.flush()
            accepted_memory = self._memory_dict(memory)
        if accepted_memory is not None:
            indexing_error = None
            try:
                from app.database.retrieval_repository import RetrievalRepository
                from app.services.retrieval_corpus import RetrievalCorpusIndexer

                RetrievalCorpusIndexer(
                    RetrievalRepository(self.session_factory)
                ).index_memory(user_id=user_id, memory=accepted_memory)
            except Exception as error:
                indexing_error = type(error).__name__
            with session_scope(self.session_factory) as session:
                memory = session.scalar(
                    select(Memory).where(
                        Memory.memory_id == accepted_memory["memory_id"],
                        Memory.user_id == user_id,
                    )
                )
                memory.retrieval_index_status = "failed" if indexing_error else "synced"
                memory.retrieval_index_error = indexing_error
                session.flush()
                accepted_memory = self._memory_dict(memory)
        return accepted_memory

    def _review_structured_memory_candidate(
        self, user_id: str, candidate_id: str, *, accept: bool,
        conflict_resolution: str | None,
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = None
        kind = ""
        with session_scope(self.session_factory) as session:
            candidate = session.scalar(select(MemoryCandidate).where(
                MemoryCandidate.candidate_id == candidate_id,
                MemoryCandidate.user_id == user_id,
            ))
            if candidate is None or candidate.status != "pending":
                raise ValueError("Memory candidate was not found or has already been reviewed.")
            resolution = conflict_resolution or ("use_new" if accept else "reject_new")
            if candidate.operation == "CONFLICT" and resolution not in {"keep_existing", "use_new", "keep_both", "reject_new"}:
                raise ValueError("Unsupported conflict resolution.")
            candidate.reviewed_at = datetime.now(timezone.utc)
            candidate.status = "accepted" if accept else "rejected"
            if not accept or resolution in {"keep_existing", "reject_new"}:
                candidate.status = "rejected"
                return None
            kind = candidate.memory_kind
            if kind == "semantic":
                existing = session.scalar(select(SemanticMemory).where(
                    SemanticMemory.semantic_memory_id == candidate.existing_entity_id,
                    SemanticMemory.user_id == user_id,
                )) if candidate.existing_entity_id else None
                if candidate.operation == "REVOKE":
                    if existing is None:
                        raise ValueError("The semantic memory selected for revocation no longer exists.")
                    existing.active = False
                    existing.revoked_at = datetime.now(timezone.utc)
                    existing.retrieval_index_status = "inactive"
                    session.execute(update(RetrievalDocument).where(
                        RetrievalDocument.user_id == user_id,
                        RetrievalDocument.corpus_type == "semantic_memory",
                        RetrievalDocument.source_entity_id.like(f"{existing.semantic_memory_id}%"),
                    ).values(active=False))
                    return self._semantic_memory_dict(existing)
                if candidate.operation in {"UPDATE", "CONFLICT"} and resolution == "use_new":
                    if existing is None:
                        raise ValueError("The semantic memory selected for update no longer exists.")
                    existing.active = False
                    existing.revoked_at = datetime.now(timezone.utc)
                    existing.retrieval_index_status = "inactive"
                    session.execute(update(RetrievalDocument).where(
                        RetrievalDocument.user_id == user_id,
                        RetrievalDocument.corpus_type == "semantic_memory",
                        RetrievalDocument.source_entity_id.like(f"{existing.semantic_memory_id}%"),
                    ).values(active=False))
                item = SemanticMemory(
                    user_id=user_id, semantic_group=candidate.semantic_group or candidate.category,
                    topic_key=candidate.topic_key,
                    value=candidate.proposed_value if candidate.proposed_value is not None else candidate.content,
                    source=candidate.source, source_conversation_id=candidate.source_conversation_id,
                    source_message_ids=list(candidate.source_message_ids or []), evidence_text=candidate.evidence_text,
                    active=True, retrieval_index_status="pending",
                    supersedes_semantic_memory_id=existing.semantic_memory_id if existing and resolution != "keep_both" else None,
                )
                session.add(item)
                session.flush()
                result = self._semantic_memory_dict(item)
            else:
                item = CareerEvent(
                    user_id=user_id, content=candidate.content, event_status=candidate.event_status or "unknown",
                    event_time=candidate.event_time, raw_temporal_expression=candidate.raw_temporal_expression,
                    source=candidate.source, source_conversation_id=candidate.source_conversation_id,
                    source_message_ids=list(candidate.source_message_ids or []), evidence_text=candidate.evidence_text,
                    active=True, retrieval_index_status="pending",
                )
                session.add(item)
                session.flush()
                result = self._career_event_dict(item)
        if result is None:
            return None
        error_name = None
        try:
            from app.database.retrieval_repository import RetrievalRepository
            from app.services.retrieval_corpus import RetrievalCorpusIndexer
            indexer = RetrievalCorpusIndexer(RetrievalRepository(self.session_factory))
            if kind == "semantic":
                indexer.index_semantic_memory(user_id=user_id, memory=result)
            else:
                indexer.index_career_event(user_id=user_id, event=result)
        except Exception as error:
            error_name = type(error).__name__
        with session_scope(self.session_factory) as session:
            if kind == "semantic":
                item = session.get(SemanticMemory, result["semantic_memory_id"])
                item.retrieval_index_status = "failed" if error_name else "synced"
                item.retrieval_index_error = error_name
                result = self._semantic_memory_dict(item)
            else:
                item = session.get(CareerEvent, result["career_event_id"])
                item.retrieval_index_status = "failed" if error_name else "synced"
                item.retrieval_index_error = error_name
                result = self._career_event_dict(item)
        return result

    def list_memories(
        self, user_id: str, *, include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            query = select(Memory).where(Memory.user_id == user_id)
            if not include_inactive:
                query = query.where(Memory.active.is_(True))
            memories = session.scalars(
                query
                .order_by(Memory.created_at.desc())
            ).all()
            return [self._memory_dict(item) for item in memories]

    def list_semantic_memories(self, user_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            query = select(SemanticMemory).where(SemanticMemory.user_id == user_id)
            if not include_inactive:
                query = query.where(SemanticMemory.active.is_(True))
            return [self._semantic_memory_dict(item) for item in session.scalars(query.order_by(SemanticMemory.created_at.desc())).all()]

    def list_career_events(self, user_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            query = select(CareerEvent).where(CareerEvent.user_id == user_id)
            if not include_inactive:
                query = query.where(CareerEvent.active.is_(True))
            return [self._career_event_dict(item) for item in session.scalars(query.order_by(CareerEvent.created_at.desc())).all()]

    # --------------------------------------------------- profile revision drafts
    def create_profile_revision_draft(
        self,
        user_id: str,
        *,
        source_type: str,
        source_conversation_id: str | None,
        source_message_ids: list[str],
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            if source_conversation_id:
                self._owned_conversation(session, user_id, source_conversation_id)
            draft = ProfileRevisionDraft(
                user=user,
                source_type=self._required_text(source_type, "source type"),
                source_conversation_id=source_conversation_id,
                source_message_ids=list(dict.fromkeys(source_message_ids)),
            )
            for raw in changes:
                draft.changes.append(
                    ProfileRevisionChange(
                        field_key=self._required_text(str(raw.get("field_key") or ""), "field key"),
                        operation=self._required_text(str(raw.get("operation") or "replace"), "operation"),
                        before_value=raw.get("before_value"),
                        proposed_value=raw.get("proposed_value"),
                        source_json=dict(raw.get("source") or {}),
                    )
                )
            session.add(draft)
            session.flush()
            return self._profile_revision_draft_dict(draft)

    def list_profile_revision_drafts(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            drafts = session.scalars(
                select(ProfileRevisionDraft)
                .where(ProfileRevisionDraft.user_id == user_id)
                .order_by(ProfileRevisionDraft.created_at.desc())
            ).all()
            return [self._profile_revision_draft_dict(item) for item in drafts]

    def review_profile_revision_change(
        self, user_id: str, change_id: str, *, accept: bool
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            change = session.scalar(
                select(ProfileRevisionChange)
                .join(ProfileRevisionDraft)
                .where(
                    ProfileRevisionChange.change_id == change_id,
                    ProfileRevisionDraft.user_id == user_id,
                )
            )
            if change is None:
                raise ValueError("Profile revision change was not found for this user.")
            if change.status not in {"pending", "accepted"}:
                raise ValueError("Profile revision change has already been reviewed.")
            current = self._profile_dict(change.draft.user).get(change.field_key)
            if accept and self._stable_json(current) != self._stable_json(change.before_value):
                change.status = "stale"
            else:
                change.status = "accepted" if accept else "rejected"
            session.flush()
            return self._profile_revision_change_dict(change)

    def apply_profile_revision_draft(
        self, user_id: str, draft_id: str
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        message_ids: list[str] = []
        conversation_id: str | None = None
        with session_scope(self.session_factory) as session:
            draft = session.scalar(
                select(ProfileRevisionDraft).where(
                    ProfileRevisionDraft.draft_id == draft_id,
                    ProfileRevisionDraft.user_id == user_id,
                )
            )
            if draft is None:
                raise ValueError("Profile revision draft was not found for this user.")
            if draft.status == "applied":
                return self._profile_revision_draft_dict(draft)
            current_profile = self._profile_dict(draft.user)
            for change in draft.changes:
                if change.status != "accepted":
                    continue
                if self._stable_json(current_profile.get(change.field_key)) != self._stable_json(change.before_value):
                    change.status = "stale"
                    continue
                changes[change.field_key] = change.proposed_value
            message_ids = list(draft.source_message_ids or [])
            conversation_id = draft.source_conversation_id
            if not changes:
                raise ValueError("No accepted non-stale Profile changes are available.")
        self.apply_profile_field_changes(
            user_id,
            changes,
            source_type="conversation",
            source_conversation_id=conversation_id,
            source_message_ids=message_ids,
        )
        with session_scope(self.session_factory) as session:
            draft = session.scalar(
                select(ProfileRevisionDraft).where(
                    ProfileRevisionDraft.draft_id == draft_id,
                    ProfileRevisionDraft.user_id == user_id,
                )
            )
            for change in draft.changes:
                if change.status == "accepted" and change.field_key in changes:
                    change.status = "applied"
            draft.status = "applied"
            draft.applied_at = datetime.now(timezone.utc)
            session.flush()
            return self._profile_revision_draft_dict(draft)

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

    def rename_conversation(
        self, user_id: str, conversation_id: str, title: str
    ) -> dict[str, Any]:
        """Rename an owned conversation without changing its identity/messages."""

        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(
                session, user_id, conversation_id
            )
            conversation.title = self._required_text(title, "title")[:200]
            conversation.updated_at = datetime.now(timezone.utc)
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

    def record_conversation_memory_signals(
        self,
        user_id: str,
        conversation_id: str,
        source_message_id: str,
        signals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Idempotently persist current-thread hints and mark extraction pending."""

        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(session, user_id, conversation_id)
            source = session.scalar(
                select(Message).where(
                    Message.message_id == source_message_id,
                    Message.conversation_id == conversation_id,
                    Message.role == "user",
                )
            )
            if source is None:
                raise ValueError("Memory signal source must be a user message in this conversation.")
            session.execute(
                delete(ConversationMemorySignal).where(
                    ConversationMemorySignal.source_message_id == source_message_id
                )
            )
            records = []
            for index, raw in enumerate(signals):
                item = ConversationMemorySignal(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    source_message_id=source_message_id,
                    signal_index=index,
                    signal_type=self._required_text(str(raw.get("type") or ""), "signal type"),
                    operation_hint=self._required_text(str(raw.get("operation_hint") or ""), "operation hint"),
                    value_hint=[str(value).strip() for value in raw.get("value_hint") or [] if str(value).strip()],
                )
                session.add(item)
                records.append(item)
            if records:
                state = session.get(ConversationMemoryState, conversation_id)
                if state is None:
                    state = ConversationMemoryState(
                        user_id=user_id,
                        conversation_id=conversation_id,
                    )
                    session.add(state)
                state.pending = True
                state.pending_boundary_message_id = source_message_id
                state.updated_at = datetime.now(timezone.utc)
            session.flush()
            return [self._conversation_memory_signal_dict(item) for item in records]

    def list_conversation_memory_signals(
        self, user_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._owned_conversation(session, user_id, conversation_id)
            records = session.scalars(
                select(ConversationMemorySignal)
                .where(
                    ConversationMemorySignal.user_id == user_id,
                    ConversationMemorySignal.conversation_id == conversation_id,
                )
                .order_by(
                    ConversationMemorySignal.created_at,
                    ConversationMemorySignal.signal_index,
                )
            ).all()
            return [self._conversation_memory_signal_dict(item) for item in records]

    def get_conversation_memory_state(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            self._owned_conversation(session, user_id, conversation_id)
            state = session.get(ConversationMemoryState, conversation_id)
            return self._conversation_memory_state_dict(state) if state else None

    def mark_conversation_extraction_pending(
        self,
        user_id: str,
        conversation_id: str,
        boundary_message_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a boundary immediately; this method never invokes an LLM."""

        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(session, user_id, conversation_id)
            state = session.get(ConversationMemoryState, conversation_id)
            messages = sorted(conversation.messages, key=lambda item: item.created_at)
            boundary = (
                next((item for item in messages if item.message_id == boundary_message_id), None)
                if boundary_message_id
                else (messages[-1] if messages else None)
            )
            if boundary is None:
                return self._conversation_memory_state_dict(state) if state else None
            if state is None:
                state = ConversationMemoryState(
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                session.add(state)
            if state.last_memory_extraction_message_id == boundary.message_id:
                session.flush()
                return self._conversation_memory_state_dict(state)
            state.pending = True
            state.pending_boundary_message_id = boundary.message_id
            state.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._conversation_memory_state_dict(state)

    def list_pending_conversation_memory_states(
        self, user_id: str
    ) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            states = session.scalars(
                select(ConversationMemoryState)
                .where(
                    ConversationMemoryState.user_id == user_id,
                    ConversationMemoryState.pending.is_(True),
                )
                .order_by(ConversationMemoryState.updated_at)
            ).all()
            return [self._conversation_memory_state_dict(item) for item in states]

    def create_memory_extraction_run(
        self,
        user_id: str,
        conversation_id: str,
        *,
        start_watermark_message_id: str | None,
        end_boundary_message_id: str,
        input_mode: str,
        input_token_count: int,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            self._owned_conversation(session, user_id, conversation_id)
            existing = session.scalar(
                select(MemoryExtractionRun).where(
                    MemoryExtractionRun.user_id == user_id,
                    MemoryExtractionRun.conversation_id == conversation_id,
                    MemoryExtractionRun.start_watermark_message_id == start_watermark_message_id,
                    MemoryExtractionRun.end_boundary_message_id == end_boundary_message_id,
                )
            )
            if existing is not None:
                if existing.status == "completed":
                    return self._memory_extraction_run_dict(existing)
                existing.attempt += 1
                existing.status = "processing"
                existing.error_summary = None
                existing.input_mode = input_mode
                existing.input_token_count = input_token_count
                session.flush()
                return self._memory_extraction_run_dict(existing)
            item = MemoryExtractionRun(
                user_id=user_id,
                conversation_id=conversation_id,
                start_watermark_message_id=start_watermark_message_id,
                end_boundary_message_id=end_boundary_message_id,
                status="processing",
                attempt=1,
                input_mode=input_mode,
                input_token_count=input_token_count,
            )
            session.add(item)
            session.flush()
            return self._memory_extraction_run_dict(item)

    def finish_memory_extraction_run(
        self,
        user_id: str,
        extraction_run_id: str,
        *,
        success: bool,
        error_summary: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            run = session.scalar(
                select(MemoryExtractionRun).where(
                    MemoryExtractionRun.extraction_run_id == extraction_run_id,
                    MemoryExtractionRun.user_id == user_id,
                )
            )
            if run is None:
                raise ValueError("Memory extraction run was not found for this user.")
            run.status = "completed" if success else "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_summary = error_summary
            state = session.get(ConversationMemoryState, run.conversation_id)
            if state is not None:
                state.processing = False
                if success:
                    state.last_memory_extraction_message_id = run.end_boundary_message_id
                    state.pending = False
                    state.pending_boundary_message_id = None
                else:
                    state.pending = True
            session.flush()
            return self._memory_extraction_run_dict(run)

    def get_effective_conversation_context(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any]:
        """Apply ordered conversation hints without mutating durable profile/memory."""

        persisted_profile = self.get_profile(user_id) or {}
        effective_profile = dict(persisted_profile)
        flexible: dict[str, list[str]] = {}
        list_fields = {"skills", "projects", "experience", "education"}
        newest_profile_write: dict[str, datetime] = {}
        for revision in self.list_profile_field_revisions(user_id):
            newest_profile_write.setdefault(
                revision["field_key"], datetime.fromisoformat(revision["created_at"])
            )
        for signal in self.list_conversation_memory_signals(user_id, conversation_id):
            signal_type = signal["type"]
            operation = signal["operation_hint"]
            values = list(signal["value_hint"])
            if signal_type.startswith("profile."):
                field = signal_type.split(".", 1)[1]
                saved_at = newest_profile_write.get(field)
                signal_at = datetime.fromisoformat(signal["created_at"])
                if saved_at is not None and signal_at <= saved_at:
                    # A later explicit Profile save/approval is authoritative for
                    # this field; stale conversation hints cannot override it.
                    continue
                if field in list_fields:
                    current = list(effective_profile.get(field) or [])
                    if operation == "replace":
                        current = values
                    elif operation == "remove":
                        removed = {value.casefold() for value in values}
                        current = [
                            item for item in current
                            if str(item).casefold() not in removed
                        ]
                    else:
                        existing = {str(item).casefold() for item in current}
                        current.extend(value for value in values if value.casefold() not in existing)
                    effective_profile[field] = current
                elif operation == "remove":
                    effective_profile[field] = None
                elif values:
                    value: Any = values[-1]
                    if field == "graduation_year" and str(value).isdigit():
                        value = int(value)
                    effective_profile[field] = value
            elif signal_type.startswith("memory."):
                current = list(flexible.get(signal_type) or [])
                if operation == "replace":
                    current = values
                elif operation == "remove":
                    removed = {value.casefold() for value in values}
                    current = [value for value in current if value.casefold() not in removed]
                else:
                    current.extend(value for value in values if value.casefold() not in {item.casefold() for item in current})
                flexible[signal_type] = current
        return {
            "persisted_profile": persisted_profile,
            "effective_profile": effective_profile,
            "current_thread_memories": flexible,
            "signals": self.list_conversation_memory_signals(user_id, conversation_id),
        }

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
    def _profile_field_keys() -> tuple[str, ...]:
        return (
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

    @staticmethod
    def _profile_value_identity(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _comparable_profile(profile: dict[str, Any]) -> dict[str, Any]:
        keys = ProfileRepository._profile_field_keys()
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

    @staticmethod
    def _conversation_memory_signal_dict(
        item: ConversationMemorySignal,
    ) -> dict[str, Any]:
        return {
            "signal_id": item.signal_id,
            "user_id": item.user_id,
            "conversation_id": item.conversation_id,
            "source_message_id": item.source_message_id,
            "signal_index": item.signal_index,
            "type": item.signal_type,
            "operation_hint": item.operation_hint,
            "value_hint": list(item.value_hint or []),
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _conversation_memory_state_dict(
        item: ConversationMemoryState,
    ) -> dict[str, Any]:
        return {
            "user_id": item.user_id,
            "conversation_id": item.conversation_id,
            "last_memory_extraction_message_id": item.last_memory_extraction_message_id,
            "pending_boundary_message_id": item.pending_boundary_message_id,
            "pending": item.pending,
            "processing": item.processing,
            "lease_expires_at": item.lease_expires_at.isoformat() if item.lease_expires_at else None,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
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
                    "retrieval_index_status": (
                        profile.current_version.retrieval_index_status
                    ),
                    "retrieval_index_error": profile.current_version.retrieval_index_error,
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
            "retrieval_index_status": version.retrieval_index_status,
            "retrieval_index_error": version.retrieval_index_error,
        }

    @staticmethod
    def _profile_field_revision_dict(
        revision: ProfileFieldRevision,
    ) -> dict[str, Any]:
        return {
            "revision_id": revision.revision_id,
            "user_id": revision.user_id,
            "field_key": revision.field_key,
            "operation": revision.operation,
            "previous_value": revision.previous_value,
            "new_value": revision.new_value,
            "source_type": revision.source_type,
            "source_conversation_id": revision.source_conversation_id,
            "source_message_ids": list(revision.source_message_ids or []),
            "resulting_profile_version_id": revision.resulting_profile_version_id,
            "created_at": revision.created_at.isoformat(),
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
            "operation": item.operation,
            "existing_memory_id": item.existing_memory_id,
            "source_conversation_id": item.source_conversation_id,
            "source_message_ids": list(item.source_message_ids or []),
            "extraction_run_id": item.extraction_run_id,
            "event_time": item.event_time.isoformat() if item.event_time else None,
            "raw_temporal_expression": item.raw_temporal_expression,
            "memory_kind": item.memory_kind,
            "existing_entity_id": item.existing_entity_id,
            "semantic_group": item.semantic_group,
            "topic_key": item.topic_key,
            "proposed_value": item.proposed_value,
            "event_status": item.event_status,
            "evidence_text": item.evidence_text,
            "evidence_start": item.evidence_start,
            "evidence_end": item.evidence_end,
            "proposal_sources": list(item.proposal_sources or []),
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
            "active": item.active,
            "supersedes_memory_id": item.supersedes_memory_id,
            "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
            "event_time": item.event_time.isoformat() if item.event_time else None,
            "source_conversation_id": item.source_conversation_id,
            "source_message_ids": list(item.source_message_ids or []),
            "retrieval_index_status": item.retrieval_index_status,
            "retrieval_index_error": item.retrieval_index_error,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _semantic_memory_dict(item: SemanticMemory) -> dict[str, Any]:
        return {
            "semantic_memory_id": item.semantic_memory_id, "memory_id": item.semantic_memory_id,
            "user_id": item.user_id, "memory_kind": "semantic", "semantic_group": item.semantic_group,
            "category": item.semantic_group, "topic_key": item.topic_key, "value": item.value,
            "content": str(item.value), "source": item.source, "active": item.active,
            "supersedes_semantic_memory_id": item.supersedes_semantic_memory_id,
            "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
            "source_conversation_id": item.source_conversation_id,
            "source_message_ids": list(item.source_message_ids or []), "evidence_text": item.evidence_text,
            "retrieval_index_status": item.retrieval_index_status,
            "retrieval_index_error": item.retrieval_index_error, "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _career_event_dict(item: CareerEvent) -> dict[str, Any]:
        return {
            "career_event_id": item.career_event_id, "memory_id": item.career_event_id,
            "user_id": item.user_id, "memory_kind": "episodic", "category": "event",
            "content": item.content, "event_status": item.event_status,
            "event_time": item.event_time.isoformat() if item.event_time else None,
            "raw_temporal_expression": item.raw_temporal_expression, "career_path_id": item.career_path_id,
            "title": item.title, "description": item.description, "start_date": item.start_date.isoformat() if item.start_date else None,
            "end_date": item.end_date.isoformat() if item.end_date else None, "outcome": item.outcome,
            "source": item.source, "active": item.active, "supersedes_event_id": item.supersedes_event_id,
            "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
            "source_conversation_id": item.source_conversation_id,
            "source_message_ids": list(item.source_message_ids or []), "evidence_text": item.evidence_text,
            "retrieval_index_status": item.retrieval_index_status,
            "retrieval_index_error": item.retrieval_index_error, "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _memory_extraction_run_dict(item: MemoryExtractionRun) -> dict[str, Any]:
        return {
            "extraction_run_id": item.extraction_run_id,
            "user_id": item.user_id,
            "conversation_id": item.conversation_id,
            "start_watermark_message_id": item.start_watermark_message_id,
            "end_boundary_message_id": item.end_boundary_message_id,
            "status": item.status,
            "attempt": item.attempt,
            "input_mode": item.input_mode,
            "input_token_count": item.input_token_count,
            "error_summary": item.error_summary,
            "created_at": item.created_at.isoformat(),
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        }

    @classmethod
    def _profile_revision_draft_dict(cls, item: ProfileRevisionDraft) -> dict[str, Any]:
        return {
            "draft_id": item.draft_id,
            "user_id": item.user_id,
            "source_type": item.source_type,
            "source_conversation_id": item.source_conversation_id,
            "source_message_ids": list(item.source_message_ids or []),
            "status": item.status,
            "created_at": item.created_at.isoformat(),
            "applied_at": item.applied_at.isoformat() if item.applied_at else None,
            "changes": [cls._profile_revision_change_dict(change) for change in item.changes],
        }

    @staticmethod
    def _profile_revision_change_dict(item: ProfileRevisionChange) -> dict[str, Any]:
        return {
            "change_id": item.change_id,
            "draft_id": item.draft_id,
            "field_key": item.field_key,
            "operation": item.operation,
            "before_value": item.before_value,
            "proposed_value": item.proposed_value,
            "status": item.status,
            "source": dict(item.source_json or {}),
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _stable_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

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
