from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.state.schema import ProfileState
from app.database.repository import profile_repository
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval_corpus import RetrievalCorpusIndexer


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    page_text = [
        text.strip()
        for page in reader.pages
        if (text := page.extract_text())
    ]
    return "\n\n".join(page_text)


def _extract_docx(path: Path) -> str:
    document = Document(str(path))
    return "\n".join(
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    )


def _extract_path(path: Path) -> str:
    """Extract one supported career document without using an LLM."""

    if not path.is_file():
        raise FileNotFoundError(f"Career document was not found: {path}")
    extension = path.suffix.lower()
    if extension == ".pdf":
        text = _extract_pdf(path)
    elif extension == ".docx":
        text = _extract_docx(path)
    else:
        raise ValueError(f"Document must be a PDF or DOCX file: {path}")

    if not text:
        raise ValueError(
            "No text could be extracted from the document. "
            "The document may contain scanned images and require OCR."
        )
    return text


def extract_resume(state: ProfileState) -> dict[str, str | list[dict[str, str]]]:
    """Deterministically extract and label one or more career documents."""

    pending = state.get("documents") or [
        {
            "path": state["resume_path"],
            "original_filename": state.get("original_filename"),
            "document_type": state.get("document_type") or "resume",
        }
    ]
    extracted: list[dict[str, str]] = []
    sections: list[str] = []
    for item in pending:
        path = Path(item["path"]).expanduser()
        text = _extract_path(path)
        filename = item.get("original_filename") or path.name
        document_type = item.get("document_type") or "other"
        extracted.append(
            {
                "filename": filename,
                "document_type": document_type,
                "text": text,
            }
        )
        sections.append(
            f"DOCUMENT: {filename}\nTYPE: {document_type}\nCONTENT:\n{text}"
        )

    user_id = state.get("user_id")
    document_ids = state.get("document_ids") or ([state["document_id"]] if state.get("document_id") else [])
    if user_id:
        indexer = RetrievalCorpusIndexer(RetrievalRepository(profile_repository.session_factory))
        for index, item in enumerate(extracted):
            if index < len(document_ids):
                indexer.index_uploaded_document(user_id=user_id, document_id=document_ids[index], document_type=item["document_type"], filename=item["filename"], text=item["text"])

    return {"resume_text": "\n\n---\n\n".join(sections), "document_texts": extracted}
