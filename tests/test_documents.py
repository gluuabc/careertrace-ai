import unittest
from io import BytesIO

from docx import Document

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.services.documents import (
    DOCX_MIME,
    PDF_MIME,
    DocumentService,
    DocumentValidationError,
    validate_document,
)


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def delete(self, key: str) -> None:
        del self.objects[key]


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("CareerTrace resume")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.repository = ProfileRepository(create_session_factory(self.engine))
        self.user = self.repository.get_or_create_user(
            "Ada Student", "ada@example.com"
        )
        self.storage = FakeStorage()
        self.service = DocumentService(self.storage, self.repository)

    def tearDown(self):
        self.engine.dispose()

    def test_upload_download_and_delete_document(self):
        data = b"%PDF-1.4\nresume"
        document = self.service.upload(
            user_id=self.user["user_id"],
            filename="../../Ada Resume.pdf",
            content_type=PDF_MIME,
            data=data,
            document_type="resume",
        )

        self.assertEqual(document["filename"], "Ada Resume.pdf")
        self.assertEqual(
            document["s3_key"],
            f"{self.user['user_id']}/{document['document_id']}/Ada_Resume.pdf",
        )
        self.assertEqual(
            self.service.download(self.user["user_id"], document["document_id"]),
            data,
        )

        self.service.delete(self.user["user_id"], document["document_id"])
        self.assertEqual(self.repository.list_documents(self.user["user_id"]), [])
        self.assertEqual(self.storage.objects, {})

    def test_accepts_valid_docx_and_rejects_mismatches(self):
        validated = validate_document(
            filename="portfolio.docx",
            content_type=DOCX_MIME,
            data=_docx_bytes(),
        )
        self.assertEqual(validated.filename, "portfolio.docx")
        self.assertEqual(validated.storage_filename, "portfolio.docx")

        with self.assertRaisesRegex(DocumentValidationError, "MIME"):
            validate_document(
                filename="portfolio.docx",
                content_type=PDF_MIME,
                data=_docx_bytes(),
            )

        with self.assertRaisesRegex(DocumentValidationError, "larger"):
            validate_document(
                filename="resume.pdf",
                content_type=PDF_MIME,
                data=b"%PDF-" + b"x" * 1024,
                max_size_mib=0,
            )

    def test_sql_failure_removes_uploaded_object(self):
        with self.assertRaisesRegex(ValueError, "Unknown user_id"):
            self.service.upload(
                user_id="missing-user",
                filename="resume.pdf",
                content_type=PDF_MIME,
                data=b"%PDF-1.4\nresume",
                document_type="resume",
            )
        self.assertEqual(self.storage.objects, {})


if __name__ == "__main__":
    unittest.main()
