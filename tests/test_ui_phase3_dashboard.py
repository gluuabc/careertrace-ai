from pathlib import Path

from app.ui import dashboard
from app.ui.components import cards
from app.ui.components.navigation import NAVIGATION_ITEMS
from app.ui.dashboard_data import load_dashboard_snapshot


class DashboardRepository:
    def __init__(self, *, pending: bool = False):
        self.user_ids: list[str] = []
        self.pending = pending

    def _scope(self, user_id: str):
        self.user_ids.append(user_id)

    def get_user(self, user_id):
        self._scope(user_id)
        return {"user_id": user_id, "name": "Avery"}

    def get_profile(self, user_id):
        self._scope(user_id)
        return {
            "school": "Example University",
            "major": "Data Science",
            "graduation_year": 2027,
            "skills": ["Python", "SQL"],
            "experience": [{"role": "Analyst"}],
            "target_roles": ["Data analyst"],
        }

    def list_semantic_memories(self, user_id):
        self._scope(user_id)
        return [
            {
                "semantic_memory_id": "m1",
                "semantic_group": "goal",
                "topic_key": "career_direction",
                "value": "Move into responsible AI",
            }
        ]

    def list_career_events(self, user_id):
        self._scope(user_id)
        return [{"career_event_id": "e1", "title": "Completed a project"}]

    def list_memory_candidates(self, user_id):
        self._scope(user_id)
        return [{"candidate_id": "c1", "status": "pending"}] if self.pending else []

    def list_profile_revision_drafts(self, user_id):
        self._scope(user_id)
        return []

    def list_conversations(self, user_id):
        self._scope(user_id)
        return [{"conversation_id": "conversation-1"}]

    def list_documents(self, user_id):
        self._scope(user_id)
        return [{"document_id": "document-1"}]

    def get_latest_analysis(self, user_id):
        self._scope(user_id)
        return {
            "strengths": ["Analytical thinking"],
            "possible_roles": ["Data analyst"],
            "recommended_next_skills": ["Data storytelling"],
        }


def test_dashboard_snapshot_uses_only_active_user_id_and_real_counts():
    repository = DashboardRepository()
    snapshot = load_dashboard_snapshot("active-user", repository=repository)

    assert repository.user_ids
    assert set(repository.user_ids) == {"active-user"}
    assert snapshot.profile_completion == 100
    assert len(snapshot.semantic_memories) == 1
    assert len(snapshot.career_events) == 1
    assert len(snapshot.conversations) == 1
    assert "Analytical thinking" in snapshot.insight
    assert "Data storytelling" in snapshot.recommendation


def test_pending_review_is_the_next_step_before_analysis_advice():
    snapshot = load_dashboard_snapshot(
        "active-user", repository=DashboardRepository(pending=True)
    )
    assert snapshot.pending_review_count == 1
    assert snapshot.recommendation == "Review 1 pending career-memory suggestion."
    assert snapshot.recommendation_metadata == "Open Memory Universe"


def test_dashboard_routes_preserve_existing_pages_and_add_home():
    keys = [item.key for item in NAVIGATION_ITEMS]
    assert keys[0] == "Dashboard"
    assert set(dashboard.TOP_LEVEL_PAGE_LABELS) == {
        "Dashboard",
        "Documents",
        "My profile",
        "Starred Q&A",
        "Memory Universe",
        "Career Assistant",
    }


def test_dynamic_card_content_is_html_escaped(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(cards.st, "html", rendered.append)
    cards.render_information_card("<script>bad()</script>", "A & B")
    assert "<script>" not in rendered[0]
    assert "&lt;script&gt;" in rendered[0]
    assert "A &amp; B" in rendered[0]


def test_theme_has_foundation_tokens_and_restrained_palette():
    theme = (
        Path(dashboard.__file__).parent / "styles" / "theme.css"
    ).read_text(encoding="utf-8")
    assert "--ct-bg: #faf9f7" in theme
    assert "--ct-blue" in theme
    assert "--ct-purple" in theme
    assert "ct-metric-card" in theme
    assert "linear-gradient" in theme


def test_dashboard_has_no_new_llm_or_write_calls():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    data_source = (Path(dashboard.__file__).parent / "dashboard_data.py").read_text(
        encoding="utf-8"
    )
    assert "get_llm(" not in data_source
    assert "respond_to_user(" not in data_source
    assert "upsert_profile(" not in data_source
    assert "load_dashboard_snapshot" in source
