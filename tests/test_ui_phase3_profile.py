from __future__ import annotations

import inspect

from app.ui import dashboard
from app.ui.components import profile as profile_components


def test_profile_page_preserves_existing_backend_workflows() -> None:
    source = inspect.getsource(dashboard._render_profile)

    for required in (
        "profile_repository.get_profile",
        "profile_repository.get_user",
        "profile_repository.list_profile_revision_drafts",
        "profile_repository.list_profile_field_history",
        "_profile_form",
        "find_profile_issues",
        "profile_mutation_service.apply_profile_field_changes",
        "_render_pending_profile_updates",
        "Save profile changes",
        "No changes detected",
        "Use this value",
    ):
        assert required in source


def test_profile_components_use_supported_fields_without_biography() -> None:
    source = inspect.getsource(profile_components)

    for field in (
        "school",
        "major",
        "graduation_year",
        "education",
        "skills",
        "experience",
        "projects",
        "courses",
        "certifications",
        "achievements",
        "career_goal",
        "target_roles",
        "preferred_locations",
        "employment_types",
        "remote_preference",
        "work_authorization",
    ):
        assert field in source

    assert "About Me" not in source
    assert 'profile.get("bio")' not in source
    assert "profile.get('bio')" not in source
    assert "semantic_memories" not in source


def test_profile_identity_escapes_persisted_values(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(profile_components.st, "html", rendered.append)

    profile_components.render_profile_identity(
        {"name": "<script>alert(1)</script>"},
        {
            "school": "A&B University",
            "major": "Computer <Engineering>",
            "graduation_year": "2029",
            "target_roles": ["AI Engineer"],
            "source_documents": [{"filename": "resume<script>.pdf"}],
        },
    )

    html = rendered[0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A&amp;B University" in html
    assert "Computer &lt;Engineering&gt;" in html


def test_profile_presentation_components_do_not_retrieve_data() -> None:
    source = inspect.getsource(profile_components)

    assert "app.database" not in source
    assert "profile_repository" not in source
    assert "get_llm" not in source


def test_profile_theme_contains_responsive_component_styles() -> None:
    theme_path = dashboard.ROOT / "app" / "ui" / "styles" / "theme.css"
    theme = theme_path.read_text(encoding="utf-8")

    for selector in (
        ".ct-profile-hero",
        ".ct-profile-orbit",
        ".ct-profile-summary-grid",
        ".ct-profile-direction-card",
        "@media (max-width: 780px)",
    ):
        assert selector in theme
