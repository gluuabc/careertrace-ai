import mimetypes
from pathlib import Path
from typing import Any

from app.services import document_service
from app.state.schema import ProfileState


def store_document(state: ProfileState) -> dict[str, Any]:
    """Store the original document before extracting any profile facts."""

    path = Path(state["resume_path"]).expanduser()
    filename = state.get("original_filename") or path.name
    content_type = state.get("content_type") or mimetypes.guess_type(filename)[0]
    if not content_type:
        raise ValueError("The document MIME type could not be determined.")
    if not state.get("user_id"):
        raise ValueError("A user_id is required before document upload.")

    document = document_service.upload(
        user_id=state["user_id"],
        filename=filename,
        content_type=content_type,
        data=path.read_bytes(),
        document_type=state.get("document_type") or "resume",
    )
    return {
        "document_id": document["document_id"],
        "s3_key": document["s3_key"],
        "original_filename": document["filename"],
        "content_type": document["content_type"],
    }
