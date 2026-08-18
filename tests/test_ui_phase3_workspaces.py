from __future__ import annotations

import inspect
from pathlib import Path

from app.ui import dashboard
from app.ui.components import assistant, documents, starred_qa
from app.ui.components.navigation import NAVIGATION_ITEMS


def test_career_assistant_keeps_every_existing_user_scoped_workflow() -> None:
    source = inspect.getsource(dashboard._render_career_assistant)
    results = inspect.getsource(dashboard._render_agent_results)
    connections = inspect.getsource(dashboard._render_connections)

    for required in (
        "list_conversations",
        "create_conversation",
        "rename_conversation",
        "get_conversation",
        "list_starred_qa_pairs",
        "star_qa_pair",
        "unstar_qa_pair",
        "respond_to_user",
        "trigger_conversation_boundary",
    ):
        assert required in source
    for required in (
        "resolve_agent_display_result",
        "job_candidates",
        "people_candidates",
        "list_resume_revision_drafts",
        "list_outreach_drafts",
        "mark_status",
    ):
        assert required in results
    for required in (
        "validate_connection_csv",
        "create_connection",
        "list_connections",
        "user_provided_email",
    ):
        assert required in connections


def test_documents_page_keeps_onboarding_and_private_storage_actions() -> None:
    upload = inspect.getsource(dashboard._render_upload)
    stored = inspect.getsource(dashboard._render_documents)
    page = inspect.getsource(dashboard._render_documents_page)

    for required in (
        "profile_graph.checkpointer.delete_thread",
        "_resume_graph",
        "Analyze documents",
        "_render_workflow",
    ):
        assert required in upload
    for required in (
        "document_service.upload",
        "document_service.download",
        "document_service.delete",
        "profile_repository.list_documents",
        "Store document",
    ):
        assert required in stored
    assert "render_documents_header" in page
    assert "_render_upload" in page


def test_starred_qa_keeps_persisted_listing_and_unstar() -> None:
    source = inspect.getsource(dashboard._render_starred_qa)

    assert "list_starred_qa_pairs" in source
    assert "unstar_qa_pair" in source
    assert "render_starred_qa_header" in source
    assert "render_starred_qa_content" in source


def test_workspace_components_escape_dynamic_content(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(documents.st, "html", rendered.append)
    documents.render_document_metadata(
        {
            "filename": "<script>bad()</script>.pdf",
            "document_type": "resume",
            "size_bytes": 1024,
            "uploaded_at": "2026-08-18",
            "profile_versions": [2],
        }
    )
    assert "<script>" not in rendered[0]
    assert "&lt;script&gt;" in rendered[0]

    rendered.clear()
    markdown: list[str] = []
    monkeypatch.setattr(starred_qa.st, "html", rendered.append)
    monkeypatch.setattr(starred_qa.st, "markdown", markdown.append)
    starred_qa.render_starred_qa_content(
        {
            "question": "<script>bad()</script>",
            "answer": "Advice & context",
            "conversation_title": "A < B",
            "created_at": "2026-08-18",
        }
    )
    assert "<script>" not in rendered[0]
    assert markdown == ["Advice & context"]


def test_workspace_components_are_presentation_only_and_share_the_theme() -> None:
    for module in (assistant, documents, starred_qa):
        source = inspect.getsource(module)
        assert "profile_repository" not in source
        assert "get_llm(" not in source

    theme = (Path(dashboard.__file__).parent / "styles" / "theme.css").read_text(
        encoding="utf-8"
    )
    for selector in (
        ".ct-assistant-header",
        ".ct-documents-header",
        ".ct-starred-header",
        ".ct-stored-document-meta",
        ".ct-starred-pair-content",
    ):
        assert selector in theme


def test_navigation_is_one_consistent_set_for_all_revised_pages() -> None:
    keys = [item.key for item in NAVIGATION_ITEMS]
    assert keys == [
        "Dashboard",
        "Memory Universe",
        "My profile",
        "Documents",
        "Career Assistant",
        "Starred Q&A",
    ]
    assert [item.label for item in NAVIGATION_ITEMS] == keys
