import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfReader

from app.nodes.documents import store_document
from app.nodes.resume import extract_resume

ROOT = Path(__file__).resolve().parents[1]


class MultiDocumentWorkflowTests(unittest.TestCase):
    def test_demo_documents_extract_together_with_type_labels(self):
        state = {
            "documents": [
                {
                    "path": str(ROOT / "demo" / "Demo_Resume.pdf"),
                    "original_filename": "Demo_Resume.pdf",
                    "document_type": "resume",
                },
                {
                    "path": str(ROOT / "demo" / "Demo_Portfolio.pdf"),
                    "original_filename": "Demo_Portfolio.pdf",
                    "document_type": "portfolio",
                },
            ]
        }

        result = extract_resume(state)

        self.assertEqual(len(result["document_texts"]), 2)
        self.assertIn("TYPE: resume", result["resume_text"])
        self.assertIn("TYPE: portfolio", result["resume_text"])
        self.assertIn("Northstar Institute of Technology", result["resume_text"])

    def test_store_node_uploads_every_document_for_the_same_user(self):
        storage_service = Mock()
        storage_service.upload.side_effect = [
            {
                "document_id": "doc-1",
                "s3_key": "user-1/doc-1/resume.pdf",
                "filename": "Demo_Resume.pdf",
                "content_type": "application/pdf",
            },
            {
                "document_id": "doc-2",
                "s3_key": "user-1/doc-2/portfolio.pdf",
                "filename": "Demo_Portfolio.pdf",
                "content_type": "application/pdf",
            },
        ]
        state = {
            "user_id": "user-1",
            "documents": [
                {
                    "path": str(ROOT / "demo" / "Demo_Resume.pdf"),
                    "original_filename": "Demo_Resume.pdf",
                    "content_type": "application/pdf",
                    "document_type": "resume",
                },
                {
                    "path": str(ROOT / "demo" / "Demo_Portfolio.pdf"),
                    "original_filename": "Demo_Portfolio.pdf",
                    "content_type": "application/pdf",
                    "document_type": "portfolio",
                },
            ],
        }

        with patch("app.nodes.documents.document_service", storage_service):
            result = store_document(state)

        self.assertEqual(result["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(storage_service.upload.call_count, 2)
        self.assertEqual(
            {
                call.kwargs["document_type"]
                for call in storage_service.upload.call_args_list
            },
            {"resume", "portfolio"},
        )

    def test_committed_demo_pdfs_are_single_page_and_synthetic(self):
        for filename in ("Demo_Resume.pdf", "Demo_Portfolio.pdf"):
            reader = PdfReader(ROOT / "demo" / filename)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertEqual(len(reader.pages), 1)
            self.assertIn("Synthetic document", text)
            self.assertIn("Maya Chen", text)


if __name__ == "__main__":
    unittest.main()
