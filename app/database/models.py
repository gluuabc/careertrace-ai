from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base
from app.database.types import PortableVector


def _uuid() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    google_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(
        String(320), unique=True, nullable=True
    )
    profile_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    preferences: Mapped["CareerPreference | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    skills: Mapped[list["Skill"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    experience: Mapped[list["Experience"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    analysis: Mapped["CareerAnalysis | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    profile_versions: Mapped[list["ProfileVersion"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    profile_field_revisions: Mapped[list["ProfileFieldRevision"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    analysis_versions: Mapped[list["CareerAnalysisVersion"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memory_candidates: Mapped[list["MemoryCandidate"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversation_memory_signals: Mapped[list["ConversationMemorySignal"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversation_memory_states: Mapped[list["ConversationMemoryState"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memory_extraction_runs: Mapped[list["MemoryExtractionRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    profile_revision_drafts: Mapped[list["ProfileRevisionDraft"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    judge_workspace_credentials: Mapped[list["JudgeWorkspaceCredential"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    agent_evidence: Mapped[list["AgentEvidence"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    connections: Mapped[list["UserConnection"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    resume_revision_drafts: Mapped[list["ResumeRevisionDraft"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    outreach_drafts: Mapped[list["OutreachDraft"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    starred_qa_pairs: Mapped[list["StarredQAPair"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    search_sessions: Mapped[list["SearchSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    retrieval_documents: Mapped[list["RetrievalDocument"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    retrieval_queries: Mapped[list["RetrievalQueryLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    model_call_metrics: Mapped[list["ModelCallMetric"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    search_phase_metrics: Mapped[list["SearchPhaseMetric"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class JudgeWorkspaceCredential(Base):
    __tablename__ = "judge_workspace_credentials"

    credential_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    recovery_code_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    user: Mapped[User] = relationship(back_populates="judge_workspace_credentials")


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    profile_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    education: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    school: Mapped[str] = mapped_column(String(300), nullable=False)
    major: Mapped[str] = mapped_column(String(300), nullable=False)
    graduation_year: Mapped[int] = mapped_column(Integer, nullable=False)
    career_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_versions.version_id", use_alter=True),
        nullable=True,
        index=True,
    )

    user: Mapped[User] = relationship(
        back_populates="profile", foreign_keys=[user_id]
    )
    current_version: Mapped["ProfileVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )


class CareerPreference(TimestampMixin, Base):
    __tablename__ = "career_preferences"

    preference_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_locations: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    employment_types: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    work_authorization: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_preference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    user: Mapped[User] = relationship(back_populates="preferences")


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_name", name="uq_skill_user_name"),
    )

    skill_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)

    user: Mapped[User] = relationship(back_populates="skills")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="projects")


class Experience(TimestampMixin, Base):
    __tablename__ = "experience"

    experience_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="experience")


class ProfileVersion(Base):
    __tablename__ = "profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "version_number", name="uq_profile_version_user_number"
        ),
    )

    version_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retrieval_index_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True
    )
    retrieval_index_error: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(
        back_populates="profile_versions", foreign_keys=[user_id]
    )
    document_sources: Mapped[list["ProfileDocumentSource"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    field_revisions: Mapped[list["ProfileFieldRevision"]] = relationship(
        back_populates="resulting_profile_version", cascade="all, delete-orphan"
    )


class ProfileFieldRevision(Base):
    """Field-scoped audit history for one internal profile snapshot mutation."""

    __tablename__ = "profile_field_revisions"

    revision_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_message_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    resulting_profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("profile_versions.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="profile_field_revisions")
    resulting_profile_version: Mapped[ProfileVersion] = relationship(
        back_populates="field_revisions"
    )


class ProfileDocumentSource(Base):
    __tablename__ = "profile_document_sources"

    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("profile_versions.version_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    profile_version: Mapped[ProfileVersion] = relationship(
        back_populates="document_sources"
    )
    document: Mapped["Document"] = relationship(back_populates="profile_sources")


class CareerAnalysis(TimestampMixin, Base):
    __tablename__ = "career_analysis"

    analysis_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("career_analysis_versions.analysis_version_id", use_alter=True),
        nullable=True,
        index=True,
    )

    user: Mapped[User] = relationship(
        back_populates="analysis", foreign_keys=[user_id]
    )
    current_version: Mapped["CareerAnalysisVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )


class CareerAnalysisVersion(Base):
    __tablename__ = "career_analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "version_number", name="uq_analysis_version_user_number"
        ),
    )

    analysis_version_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("profile_versions.version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="analysis_versions")
    profile_version: Mapped[ProfileVersion] = relationship()


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="documents")
    profile_sources: Mapped[list[ProfileDocumentSource]] = relationship(
        back_populates="document"
    )


class MemoryCandidate(Base):
    __tablename__ = "memory_candidates"

    candidate_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), default="ADD", nullable=False, index=True)
    existing_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.memory_id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    extraction_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_temporal_expression: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="memory_candidates")


class Memory(Base):
    __tablename__ = "memories"

    memory_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.memory_id", ondelete="SET NULL"), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    retrieval_index_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True
    )
    retrieval_index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="memories")


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    context_summaries: Mapped[list["ConversationContextSummary"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    memory_signals: Mapped[list["ConversationMemorySignal"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    memory_state: Mapped["ConversationMemoryState | None"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )
    memory_extraction_runs: Mapped[list["MemoryExtractionRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    profile_revision_drafts: Mapped[list["ProfileRevisionDraft"]] = relationship(
        back_populates="conversation"
    )


class Message(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.message_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    reply_to: Mapped["Message | None"] = relationship(
        remote_side=[message_id], foreign_keys=[reply_to_message_id]
    )


class ConversationMemorySignal(Base):
    """Conversation-scoped working signal; never a profile or approved memory write."""

    __tablename__ = "conversation_memory_signals"
    __table_args__ = (
        UniqueConstraint(
            "source_message_id", "signal_index", name="uq_conversation_signal_message_index"
        ),
    )

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_index: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operation_hint: Mapped[str] = mapped_column(String(30), nullable=False)
    value_hint: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="conversation_memory_signals")
    conversation: Mapped[Conversation] = relationship(back_populates="memory_signals")


class ConversationMemoryState(TimestampMixin, Base):
    __tablename__ = "conversation_memory_states"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_memory_extraction_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True
    )
    pending_boundary_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True
    )
    pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    processing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="conversation_memory_states")
    conversation: Mapped[Conversation] = relationship(back_populates="memory_state")


class MemoryExtractionRun(Base):
    __tablename__ = "memory_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "start_watermark_message_id",
            "end_boundary_message_id",
            name="uq_memory_extraction_segment",
        ),
    )

    extraction_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    start_watermark_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True)
    end_boundary_message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    input_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    input_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="memory_extraction_runs")
    conversation: Mapped[Conversation] = relationship(back_populates="memory_extraction_runs")


class ProfileRevisionDraft(Base):
    __tablename__ = "profile_revision_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True, index=True)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="profile_revision_drafts")
    conversation: Mapped[Conversation | None] = relationship(back_populates="profile_revision_drafts")
    changes: Mapped[list["ProfileRevisionChange"]] = relationship(back_populates="draft", cascade="all, delete-orphan")


class ProfileRevisionChange(Base):
    __tablename__ = "profile_revision_changes"

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    draft_id: Mapped[str] = mapped_column(ForeignKey("profile_revision_drafts.draft_id", ondelete="CASCADE"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    before_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    draft: Mapped[ProfileRevisionDraft] = relationship(back_populates="changes")


class StarredQAPair(Base):
    __tablename__ = "starred_qa_pairs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "user_message_id",
            "assistant_message_id",
            name="uq_starred_qa_user_pair",
        ),
    )

    starred_qa_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False
    )
    preference_update_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="starred_qa_pairs")
    conversation: Mapped[Conversation] = relationship()
    user_message: Mapped[Message] = relationship(foreign_keys=[user_message_id])
    assistant_message: Mapped[Message] = relationship(foreign_keys=[assistant_message_id])


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="running", nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True, index=True
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.message_id", ondelete="SET NULL"), nullable=True, index=True
    )
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="agent_runs")
    conversation: Mapped[Conversation] = relationship(back_populates="agent_runs")
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["AgentEvidence"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    search_sessions: Mapped[list["SearchSession"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SearchSession(TimestampMixin, Base):
    """Durable search state shared across graph iterations and process restarts."""

    __tablename__ = "search_sessions"

    search_session_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    intent: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    normalized_request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    source_call_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    source_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remaining_source_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_no_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    visited_sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_cursors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    seen_candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    candidate_records: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    query_variants: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_failures: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source_coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False, index=True)

    run: Mapped[AgentRun] = relationship(back_populates="search_sessions")
    user: Mapped[User] = relationship(back_populates="search_sessions")
    sources: Mapped[list["SearchSourceProgress"]] = relationship(
        back_populates="search_session", cascade="all, delete-orphan"
    )


class SearchSourceProgress(Base):
    __tablename__ = "search_source_progress"
    __table_args__ = (
        UniqueConstraint("search_session_id", "source_key", name="uq_search_source_session_key"),
    )

    source_progress_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    search_session_id: Mapped[str] = mapped_column(
        ForeignKey("search_sessions.search_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(500), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    company_or_domain: Mapped[str | None] = mapped_column(String(500), nullable=True)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_more: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    search_session: Mapped[SearchSession] = relationship(back_populates="sources")


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_number", name="uq_agent_step_run_sequence"),
    )

    step_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    display_summary: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[AgentRun] = relationship(back_populates="steps")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(back_populates="step")


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    tool_call_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_steps.step_id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sanitized_arguments_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_number: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")
    step: Mapped[AgentStep | None] = relationship(back_populates="tool_calls")


class AgentEvidence(Base):
    __tablename__ = "agent_evidence"

    evidence_id: Mapped[str] = mapped_column(String(39), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    structured_content: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="agent_evidence")
    run: Mapped[AgentRun] = relationship(back_populates="evidence")


class RetrievalDocument(TimestampMixin, Base):
    __tablename__ = "retrieval_documents"
    __table_args__ = (
        UniqueConstraint(
            "corpus_type", "user_id", "source_entity_id", "source_version", "content_hash",
            name="uq_retrieval_document_source_version_hash",
        ),
    )

    retrieval_document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    corpus_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_entity_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    embedding_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(PortableVector(1024), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    user: Mapped[User | None] = relationship(back_populates="retrieval_documents")


class RetrievalQueryLog(Base):
    __tablename__ = "retrieval_query_logs"

    retrieval_query_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    corpus_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rankings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates="retrieval_queries")


class ModelCallMetric(Base):
    __tablename__ = "model_call_metrics"

    model_call_metric_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rough_estimated_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preflight_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preflight_count_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_context_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_margin_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compression_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compression_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates="model_call_metrics")


class SearchPhaseMetric(Base):
    """Privacy-safe timing metadata for one internal search phase."""

    __tablename__ = "search_phase_metrics"

    search_phase_metric_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True, index=True
    )
    search_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_sessions.search_session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phase: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    embedding_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding_cache_hit_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="search_phase_metrics")


class ConversationContextSummary(Base):
    __tablename__ = "conversation_context_summaries"

    summary_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    covered_through_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="context_summaries")


class UserConnection(TimestampMixin, Base):
    __tablename__ = "user_connections"

    connection_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    current_role: Mapped[str | None] = mapped_column(String(300), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    education: Mapped[str | None] = mapped_column(Text, nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_provided_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    user: Mapped[User] = relationship(back_populates="connections")


class ResumeRevisionDraft(TimestampMixin, Base):
    __tablename__ = "resume_revision_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("profile_versions.version_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_document_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    target_job_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates="resume_revision_drafts")
    changes: Mapped[list["ResumeRevisionChange"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )


class ResumeRevisionChange(Base):
    __tablename__ = "resume_revision_changes"

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("resume_revision_drafts.draft_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_identifier: Mapped[str | None] = mapped_column(String(300), nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    profile_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    job_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    draft: Mapped[ResumeRevisionDraft] = relationship(back_populates="changes")


class OutreachDraft(TimestampMixin, Base):
    __tablename__ = "outreach_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    outreach_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    recipient_candidate_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recipient_name: Mapped[str] = mapped_column(String(300), nullable=False)
    recipient_role: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recipient_organization: Mapped[str | None] = mapped_column(String(300), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    relevant_connections: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    previous_draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("outreach_drafts.draft_id", ondelete="SET NULL"), nullable=True, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates="outreach_drafts")
