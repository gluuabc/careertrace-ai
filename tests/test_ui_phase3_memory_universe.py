import inspect
from pathlib import Path

from app.ui import dashboard
from app.ui.components import cards, memory_cards, memory_universe


def test_memory_universe_page_uses_existing_user_scoped_interfaces():
    source = inspect.getsource(dashboard._render_memory)
    for method in (
        "list_semantic_memories",
        "list_career_events",
        "list_memory_candidates",
        "list_profile_revision_drafts",
        "review_memory_candidate",
        "_render_pending_profile_updates",
    ):
        assert method in source
    assert "get_llm(" not in source
    assert "create_memory_candidate" not in source


def test_memory_page_has_all_requested_sections():
    source = inspect.getsource(dashboard._render_memory)
    assert "render_memory_page_header" in source
    assert "render_memory_universe" in inspect.getsource(
        dashboard._render_memory_universe_overview
    )
    assert "Semantic memory" in source
    assert "Episodic career memory timeline" in source
    assert "Memory inbox" in source
    assert "Memory control" in source


def test_universe_escapes_user_content_and_has_no_javascript(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(memory_universe.st, "html", rendered.append)
    memory_universe.render_memory_universe(
        [
            {
                "semantic_group": "skill",
                "value": "<script>bad()</script>",
                "source": "Resume & conversation",
                "created_at": "2026-08-18T00:00:00+00:00",
            }
        ],
        [],
    )
    html = rendered[0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Resume &amp; conversation" in html
    assert "unsafe_allow_javascript" not in html


def test_semantic_panel_uses_real_metadata_and_marks_absent_confidence(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(memory_cards.st, "html", rendered.append)
    memory_cards.render_semantic_memory_panel(
        [
            {
                "semantic_group": "goal",
                "topic_key": "career_direction",
                "value": "AI research",
                "source": "conversation",
                "created_at": "2026-08-18T00:00:00+00:00",
            }
        ]
    )
    html = rendered[0]
    assert "AI research" in html
    assert "conversation" in html
    assert "Aug 2026" in html
    assert "Not recorded" in html


def test_memory_header_uses_supplied_counts_only(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(cards.st, "html", rendered.append)
    cards.render_memory_page_header(
        total_memories=7, career_events=2, pending_reviews=1
    )
    html = rendered[0]
    assert ">7<" in html
    assert ">2<" in html
    assert ">1<" in html
    assert "1,248" not in html


def test_memory_styles_live_in_shared_theme():
    theme = (
        Path(dashboard.__file__).parent / "styles" / "theme.css"
    ).read_text(encoding="utf-8")
    assert ".ct-memory-universe" in theme
    assert ".ct-low-poly-core" in theme
    assert ".ct-semantic-grid" in theme
    assert ".ct-inbox-candidate" in theme
    assert ".ct-memory-control-list" in theme
    assert "@media (max-width: 780px)" in theme


def test_memory_overview_does_not_duplicate_inline_css():
    source = inspect.getsource(dashboard._render_memory_universe_overview)
    assert "<style>" not in source
    assert "render_memory_universe" in source
