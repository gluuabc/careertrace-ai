import os
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.database.repository import ProfileRepository, profile_repository
from app.storage.base import ObjectStorage
from app.storage.s3 import S3ObjectStorage

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
ALLOWED_DOCUMENTS = {
    ".pdf": PDF_MIME,
    ".docx": DOCX_MIME,
}
ALLOWED_DOCUMENT_TYPES = {
    "resume",
    "portfolio",
    "transcript",
    "certificate",
    "other",
}
HARD_MAX_DOCUMENT_SIZE_MIB = 10


class DocumentValidationError(ValueError):
    """Raised before any S3 or SQL write for an unsafe document."""


@dataclass(frozen=True)
class ValidatedDocument:
    filename: str
    storage_filename: str
    content_type: str
    data: bytes


def _safe_filename(filename: str) -> str:
    basename = Path(filename).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return safe or "document"


def _is_docx(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
        return "[Content_Types].xml" in names and "word/document.xml" in names
    except zipfile.BadZipFile:
        return False


def validate_document(
    *,
    filename: str,
    content_type: str,
    data: bytes,
    max_size_mib: int | None = None,
) -> ValidatedDocument:
    """Validate extension, declared MIME, signature, and configured size."""

    extension = Path(filename).suffix.lower()
    expected_mime = ALLOWED_DOCUMENTS.get(extension)
    if expected_mime is None:
        raise DocumentValidationError("Only PDF and DOCX documents are allowed.")
    if content_type != expected_mime:
        raise DocumentValidationError(
            f"The MIME type does not match the {extension} extension."
        )

    configured_limit = (
        int(os.getenv("MAX_DOCUMENT_SIZE_MIB", "10"))
        if max_size_mib is None
        else max_size_mib
    )
    size_limit = min(configured_limit, HARD_MAX_DOCUMENT_SIZE_MIB)
    if not data:
        raise DocumentValidationError("The uploaded document is empty.")
    if len(data) > size_limit * 1024 * 1024:
        raise DocumentValidationError(
            f"Documents must be no larger than {size_limit} MiB."
        )

    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise DocumentValidationError("The uploaded file is not a valid PDF.")
    if extension == ".docx" and not _is_docx(data):
        raise DocumentValidationError("The uploaded file is not a valid DOCX.")

    return ValidatedDocument(
        filename=Path(filename).name,
        storage_filename=_safe_filename(filename),
        content_type=expected_mime,
        data=data,
    )


class DocumentService:
    """Coordinate S3 objects and SQL metadata with compensating cleanup."""

    def __init__(
        self,
        storage: ObjectStorage | None = None,
        repository: ProfileRepository = profile_repository,
    ):
        self.storage = storage or S3ObjectStorage()
        self.repository = repository

    def upload(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str,
        data: bytes,
        document_type: str,
    ) -> dict[str, Any]:
        if document_type not in ALLOWED_DOCUMENT_TYPES:
            raise DocumentValidationError(
                "Unsupported career document type."
            )
        validated = validate_document(
            filename=filename,
            content_type=content_type,
            data=data,
        )
        document_id = str(uuid4())
        s3_key = f"{user_id}/{document_id}/{validated.storage_filename}"

        self.storage.put(s3_key, validated.data, validated.content_type)
        try:
            return self.repository.create_document(
                document_id=document_id,
                user_id=user_id,
                filename=validated.filename,
                s3_key=s3_key,
                document_type=document_type,
                content_type=validated.content_type,
                size_bytes=len(validated.data),
            )
        except Exception:
            self.storage.delete(s3_key)
            raise

    def download(self, user_id: str, document_id: str) -> bytes:
        document = self.repository.get_document(user_id, document_id)
        return self.storage.get(document["s3_key"])

    def delete(self, user_id: str, document_id: str) -> None:
        document = self.repository.get_document(user_id, document_id)
        original = self.storage.get(document["s3_key"])
        self.storage.delete(document["s3_key"])
        try:
            self.repository.delete_document(user_id, document_id)
        except Exception:
            self.storage.put(
                document["s3_key"], original, document["content_type"]
            )
            raise


document_service = DocumentService()
