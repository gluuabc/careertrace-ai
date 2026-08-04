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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    user: Mapped[User] = relationship(
        back_populates="profile_versions", foreign_keys=[user_id]
    )
    document_sources: Mapped[list["ProfileDocumentSource"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
