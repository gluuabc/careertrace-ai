"""Application services coordinating external systems and SQL."""

from typing import Any

__all__ = ["document_service", "respond_to_user"]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid circular imports between the Career Agent tool registry
    # and its service implementations.
    if name == "document_service":
        from app.services.documents import document_service

        return document_service
    if name == "respond_to_user":
        from app.services.career_assistant import respond_to_user

        return respond_to_user
    raise AttributeError(name)
