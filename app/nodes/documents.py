import mimetypes
from pathlib import Path
from typing import Any

from app.services import document_service
from app.state.schema import ProfileState


def _pending_documents(state: ProfileState) -> list[dict[str, Any]]:
    if state.get("documents"):
        return list(state["documents"])
    return [
        {
            "path": state["resume_path"],
            "original_filename": state.get("original_filename"),
            "content_type": state.get("content_type"),
            "document_type": state.get("document_type") or "resume",
        }
    ]


def store_document(state: ProfileState) -> dict[str, Any]:
    """Store every original document before extracting candidate facts."""

    if not state.get("user_id"):
        raise ValueError("A user_id is required before document upload.")

    stored: list[dict[str, Any]] = []
    try:
        for pending in _pending_documents(state):
            path = Path(pending["path"]).expanduser()
            filename = pending.get("original_filename") or path.name
            content_type = pending.get("content_type") or mimetypes.guess_type(
                filename
            )[0]
            if not content_type:
                raise ValueError("The document MIME type could not be determined.")
            stored.append(
                document_service.upload(
                    user_id=state["user_id"],
                    filename=filename,
                    content_type=content_type,
                    data=path.read_bytes(),
                    document_type=pending.get("document_type") or "other",
                )
            )
    except Exception:
        for document in reversed(stored):
            document_service.delete(state["user_id"], document["document_id"])
        raise

    return {
        "document_ids": [item["document_id"] for item in stored],
        "stored_documents": stored,
        # Preserve singular keys for CLI/backward compatibility.
        "document_id": stored[0]["document_id"],
        "s3_key": stored[0]["s3_key"],
    }
