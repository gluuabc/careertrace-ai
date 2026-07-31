from pathlib import Path

from pypdf import PdfReader

from app.state.schema import ProfileState


def extract_resume(state: ProfileState) -> dict[str, str]:
    """Deterministically extract text from the local PDF in graph state."""

    resume_path = Path(state["resume_path"]).expanduser()

    if not resume_path.is_file():
        raise FileNotFoundError(f"Resume PDF was not found: {resume_path}")
    if resume_path.suffix.lower() != ".pdf":
        raise ValueError(f"Resume must be a PDF file: {resume_path}")

    reader = PdfReader(str(resume_path))
    page_text = [
        text.strip()
        for page in reader.pages
        if (text := page.extract_text())
    ]
    resume_text = "\n\n".join(page_text)

    if not resume_text:
        raise ValueError(
            "No text could be extracted from the resume. "
            "The PDF may contain scanned images and require OCR."
        )

    return {"resume_text": resume_text}