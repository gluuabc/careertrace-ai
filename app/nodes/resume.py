from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.state.schema import ProfileState


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


def extract_resume(state: ProfileState) -> dict[str, str]:
    """Deterministically extract text from a validated PDF or DOCX."""

    resume_path = Path(state["resume_path"]).expanduser()

    if not resume_path.is_file():
        raise FileNotFoundError(f"Resume PDF was not found: {resume_path}")
    extension = resume_path.suffix.lower()
    if extension == ".pdf":
        resume_text = _extract_pdf(resume_path)
    elif extension == ".docx":
        resume_text = _extract_docx(resume_path)
    else:
        raise ValueError(f"Resume must be a PDF or DOCX file: {resume_path}")

    if not resume_text:
        raise ValueError(
            "No text could be extracted from the resume. "
            "The document may contain scanned images and require OCR."
        )

    return {"resume_text": resume_text}
