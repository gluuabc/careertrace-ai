"""Reusable, data-agnostic Streamlit presentation components."""

from app.ui.components.cards import (
    render_information_card,
    render_insight_card,
    render_metric_card,
    render_memory_page_header,
    render_page_header,
)
from app.ui.components.assistant import (
    render_agent_activity_intro,
    render_assistant_header,
    render_assistant_section,
    render_result_heading,
)
from app.ui.components.documents import (
    render_demo_assets_intro,
    render_document_metadata,
    render_documents_header,
    render_documents_section,
)
from app.ui.components.memory_cards import (
    render_memory_card,
    render_memory_candidate_summary,
    render_semantic_memory_panel,
)
from app.ui.components.memory_universe import render_memory_universe
from app.ui.components.navigation import render_sidebar_navigation
from app.ui.components.planet import render_career_planet
from app.ui.components.profile import (
    render_career_direction,
    render_profile_identity,
    render_profile_orbit,
    render_profile_summary,
)
from app.ui.components.timeline import render_timeline
from app.ui.components.starred_qa import (
    render_starred_empty_state,
    render_starred_qa_content,
    render_starred_qa_header,
)

__all__ = [
    "render_career_planet",
    "render_agent_activity_intro",
    "render_assistant_header",
    "render_assistant_section",
    "render_career_direction",
    "render_information_card",
    "render_insight_card",
    "render_memory_card",
    "render_memory_candidate_summary",
    "render_memory_universe",
    "render_semantic_memory_panel",
    "render_metric_card",
    "render_demo_assets_intro",
    "render_document_metadata",
    "render_documents_header",
    "render_documents_section",
    "render_memory_page_header",
    "render_page_header",
    "render_profile_identity",
    "render_profile_orbit",
    "render_profile_summary",
    "render_sidebar_navigation",
    "render_result_heading",
    "render_starred_empty_state",
    "render_starred_qa_content",
    "render_starred_qa_header",
    "render_timeline",
]
