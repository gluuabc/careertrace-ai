import inspect
import unittest

from sqlalchemy import func, select

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.models import CareerAnalysisVersion, Experience, ProfileVersion, Project, RetrievalDocument, Skill
from app.database.repository import ProfileRepository
from app.database.retrieval_repository import RetrievalRepository
from app.services.profile_mutation import ProfileMutationService, is_profile_field_change_stale
from app.services.retrieval import HybridRetrievalService
from app.services.retrieval_corpus import RetrievalCorpusIndexer
from app.ui import dashboard


class FakeEmbeddings:
    model_id = "fake"
    dimensions = 1024
    last_input_tokens = 2

    def embed(self, text):
        return [float(len(text) % 7)] + [0.0] * 1023


def profile(**changes):
    base = {
        "school": "Example University",
        "major": "Computer Science",
        "graduation_year": 2028,
        "skills": ["Python"],
        "projects": [{"title": "CareerTrace", "description": "Assistant"}],
        "experience": [{"organization": "Lab", "role": "Intern", "description": "Built tools"}],
    }
    return {**base, **changes}


class PhaseOneProfileFieldTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        self.factory = create_session_factory(self.engine)
        self.repository = ProfileRepository(self.factory)
        self.user = self.repository.get_or_create_user("Ada", "ada@example.com")
        retrieval_repository = RetrievalRepository(self.factory)
        indexer = RetrievalCorpusIndexer(
            retrieval_repository,
            HybridRetrievalService(retrieval_repository, FakeEmbeddings()),
        )
        self.service = ProfileMutationService(self.repository, indexer)
        self.first = self.service.apply_profile_field_changes(
            self.user["user_id"], profile(), source_type="manual"
        )

    def tearDown(self):
        self.engine.dispose()

    def test_profile_field_change_creates_only_changed_field_history(self):
        changed = self.service.apply_profile_field_changes(
            self.user["user_id"], {"skills": ["Python", "SQL"]}, source_type="manual"
        )
        revisions = self.repository.list_profile_field_revisions(self.user["user_id"])
        latest = [item for item in revisions if item["resulting_profile_version_id"] == changed["profile_version_id"]]
        self.assertEqual([item["field_key"] for item in latest], ["skills"])
        history = self.repository.list_profile_field_history(self.user["user_id"])
        self.assertEqual(history["school"], [])

    def test_unchanged_profile_field_creates_no_field_revision(self):
        before = len(self.repository.list_profile_field_revisions(self.user["user_id"]))
        result = self.service.apply_profile_field_changes(
            self.user["user_id"], {"major": "Computer Science"}, source_type="manual"
        )
        self.assertFalse(result["profile_changed"])
        self.assertEqual(len(self.repository.list_profile_field_revisions(self.user["user_id"])), before)

    def test_multiple_changed_fields_create_one_internal_profile_snapshot_and_per_field_revisions(self):
        before = len(self.repository.list_profile_versions(self.user["user_id"]))
        result = self.service.apply_profile_field_changes(
            self.user["user_id"], {"major": "AI", "graduation_year": 2029}, source_type="manual"
        )
        self.assertEqual(len(self.repository.list_profile_versions(self.user["user_id"])), before + 1)
        self.assertEqual({item["field_key"] for item in result["field_revisions"]}, {"major", "graduation_year"})

    def test_profile_field_restore_changes_only_selected_field(self):
        self.service.apply_profile_field_changes(self.user["user_id"], {"major": "AI"}, source_type="manual")
        restored = self.service.apply_profile_field_changes(
            self.user["user_id"], {"major": "Computer Science"}, source_type="history_restore"
        )
        self.assertEqual(restored["major"], "Computer Science")
        self.assertEqual(restored["school"], "Example University")
        self.assertEqual([item["field_key"] for item in restored["field_revisions"]], ["major"])

    def test_profile_field_restore_creates_new_internal_snapshot(self):
        changed = self.service.apply_profile_field_changes(self.user["user_id"], {"major": "AI"}, source_type="manual")
        restored = self.service.apply_profile_field_changes(self.user["user_id"], {"major": "Computer Science"}, source_type="history_restore")
        self.assertEqual(restored["profile_version"], changed["profile_version"] + 1)
        self.assertNotEqual(restored["profile_version_id"], self.first["profile_version_id"])

    def test_profile_field_change_syncs_normalized_skills(self):
        self.service.apply_profile_field_changes(self.user["user_id"], {"skills": ["Rust", "SQL"]}, source_type="manual")
        with self.factory() as session:
            values = session.scalars(select(Skill.skill_name).where(Skill.user_id == self.user["user_id"])).all()
        self.assertEqual(set(values), {"Rust", "SQL"})

    def test_profile_field_change_syncs_projects_and_experience(self):
        self.service.apply_profile_field_changes(
            self.user["user_id"],
            {"projects": [{"title": "New", "description": "New work"}], "experience": [{"organization": "Co", "role": "Engineer", "description": "Shipped"}]},
            source_type="manual",
        )
        with self.factory() as session:
            projects = session.scalars(select(Project).where(Project.user_id == self.user["user_id"])).all()
            experience = session.scalars(select(Experience).where(Experience.user_id == self.user["user_id"])).all()
        self.assertEqual([(item.title, item.description) for item in projects], [("New", "New work")])
        self.assertEqual([(item.organization, item.role) for item in experience], [("Co", "Engineer")])

    def test_profile_field_change_refreshes_current_retrieval_representation(self):
        result = self.service.apply_profile_field_changes(self.user["user_id"], {"major": "Artificial Intelligence"}, source_type="manual")
        with self.factory() as session:
            rows = session.scalars(select(RetrievalDocument).where(RetrievalDocument.user_id == self.user["user_id"], RetrievalDocument.active.is_(True), RetrievalDocument.corpus_type == "resume")).all()
        self.assertTrue(any("Artificial Intelligence" in item.text for item in rows))
        self.assertEqual(result["retrieval_index_status"], "ready")

    def test_old_profile_retrieval_is_not_current_after_field_change(self):
        old_version = self.first["profile_version_id"]
        self.service.apply_profile_field_changes(self.user["user_id"], {"major": "AI"}, source_type="manual")
        with self.factory() as session:
            old_active = session.scalar(select(func.count()).select_from(RetrievalDocument).where(RetrievalDocument.user_id == self.user["user_id"], RetrievalDocument.source_version == old_version, RetrievalDocument.active.is_(True)))
        self.assertEqual(old_active, 0)

    def test_noop_profile_edit_creates_no_false_version_or_revision(self):
        versions = len(self.repository.list_profile_versions(self.user["user_id"]))
        revisions = len(self.repository.list_profile_field_revisions(self.user["user_id"]))
        result = self.service.apply_profile_field_changes(self.user["user_id"], profile(), source_type="manual")
        self.assertFalse(result["profile_changed"])
        self.assertEqual(len(self.repository.list_profile_versions(self.user["user_id"])), versions)
        self.assertEqual(len(self.repository.list_profile_field_revisions(self.user["user_id"])), revisions)

    def test_unrelated_field_change_does_not_stale_pending_field_change(self):
        current = {**profile(), "school": "Different University"}
        self.assertFalse(is_profile_field_change_stale(field_key="major", before_value="Computer Science", current_profile=current))

    def test_same_field_change_marks_pending_change_stale(self):
        self.assertTrue(is_profile_field_change_stale(field_key="major", before_value="Computer Science", current_profile={**profile(), "major": "AI"}))

    def test_existing_career_analysis_data_is_not_deleted(self):
        self.repository.save_analysis(self.user["user_id"], {"strengths": ["Python"]})
        self.service.apply_profile_field_changes(self.user["user_id"], {"major": "AI"}, source_type="manual")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CareerAnalysisVersion)), 1)


class PhaseOneUIContractTests(unittest.TestCase):
    def test_whole_profile_rollback_is_not_exposed_in_normal_ui(self):
        source = inspect.getsource(dashboard._render_memory)
        self.assertNotIn("rollback_profile", source)
        self.assertNotIn("list_profile_versions", source)

    def test_career_analysis_is_not_rendered_in_active_ui(self):
        source = inspect.getsource(dashboard.main)
        self.assertNotIn("_render_analysis", source)
        self.assertNotIn("Career analysis", dashboard.TOP_LEVEL_PAGE_LABELS)

    def test_documents_page_contains_upload_and_stored_document_tabs(self):
        self.assertEqual(dashboard.DOCUMENT_PAGE_SECTIONS, ("Upload & Analyze", "Stored Documents"))

    def test_document_upload_flow_works_after_page_merge(self):
        source = inspect.getsource(dashboard._render_documents_page)
        self.assertIn("_render_upload", source)

    def test_no_duplicate_document_upload_navigation_item(self):
        self.assertEqual(dashboard.TOP_LEVEL_PAGE_LABELS.count("Documents"), 1)
        self.assertNotIn("Document upload", dashboard.TOP_LEVEL_PAGE_LABELS)


if __name__ == "__main__":
    unittest.main()
