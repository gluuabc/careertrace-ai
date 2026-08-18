from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.memory_retrieval import ProgressiveMemoryService
from app.ui import dashboard


class EmptyRetrieval:
    def retrieve(self, **kwargs):
        return SimpleNamespace(items=[])


@pytest.fixture
def workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Ada")
    profile = repository.upsert_profile(
        user["user_id"],
        {
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "skills": ["Python", "SQL"],
            "experience": [{"organization": "Lab", "role": "Intern"}],
            "projects": [{"title": "CareerTrace", "description": "Career assistant"}],
        },
    )
    conversation = repository.create_conversation(user["user_id"], "Progressive")
    service = ProgressiveMemoryService(repository, EmptyRetrieval())
    yield repository, user, profile, conversation, service
    engine.dispose()


def _approve(
    repository: ProfileRepository,
    user_id: str,
    content: str,
    *,
    category: str = "preference",
    event_time=None,
    conversation_id=None,
    message_ids=None,
):
    candidate = repository.create_memory_candidate(
        user_id,
        category=category,
        content=content,
        confidence=1,
        source="conversation",
        source_conversation_id=conversation_id,
        source_message_ids=message_ids,
        event_time=event_time,
    )
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        return repository.review_memory_candidate(user_id, candidate["candidate_id"], accept=True)


def test_profile_exact_field_lookup_precedes_memory_vector_retrieval(workspace):
    repository, user, _, conversation, _ = workspace
    _approve(repository, user["user_id"], "Prefer remote internships")
    calls = []

    class RecordingRetrieval(EmptyRetrieval):
        def retrieve(self, **kwargs):
            calls.append("memory_vector")
            return super().retrieve(**kwargs)

    service = ProgressiveMemoryService(repository, RecordingRetrieval())
    original = repository.get_effective_conversation_context

    def exact_profile(*args, **kwargs):
        calls.append("profile_sql")
        return original(*args, **kwargs)

    with patch.object(repository, "get_effective_conversation_context", side_effect=exact_profile):
        result = service.build_context(
            user_id=user["user_id"], conversation_id=conversation["conversation_id"],
            intent="job_search", query="Find internships",
        )
    assert calls.index("profile_sql") < calls.index("memory_vector")
    assert result["profile"]["major"] == "Computer Science"


def test_memory_catalog_returns_compact_cards_only(workspace):
    repository, user, _, _, service = workspace
    _approve(repository, user["user_id"], "Prefer remote AI roles")
    card = service.memory_catalog(
        user_id=user["user_id"], query="remote jobs", intent="job_search"
    )[0]
    assert set(card) == {
        "memory_id", "type", "title", "short_description", "updated_at", "provenance"
    }
    assert "source_message_ids" not in card and "content" not in card


def test_memory_catalog_is_bounded_to_ten(workspace):
    repository, user, _, _, service = workspace
    for index in range(12):
        _approve(repository, user["user_id"], f"Preference {index}")
    cards = service.memory_catalog(
        user_id=user["user_id"], query="Find jobs", intent="job_search", limit=50
    )
    assert len(cards) == 10


def test_memory_details_accepts_at_most_three_ids(workspace):
    repository, user, _, _, service = workspace
    ids = [_approve(repository, user["user_id"], f"Preference {index}")["memory_id"] for index in range(4)]
    with pytest.raises(ValueError, match="three"):
        service.get_memory_details(user_id=user["user_id"], memory_ids=ids)


def test_memory_source_context_is_loaded_only_when_requested(workspace):
    _, user, _, conversation, service = workspace
    with patch.object(service, "get_memory_source_context") as source:
        service.build_context(
            user_id=user["user_id"], conversation_id=conversation["conversation_id"],
            intent="job_search", query="Find jobs", include_source_context=False,
        )
    source.assert_not_called()


def test_memory_source_context_is_bounded(workspace):
    repository, user, _, conversation, service = workspace
    source_ids = []
    for index in range(9):
        message = repository.add_message(
            user["user_id"], conversation["conversation_id"],
            "user" if index % 2 == 0 else "assistant", f"message {index}",
        )
        if index in {0, 4, 8}:
            source_ids.append(message["message_id"])
    memory = _approve(
        repository, user["user_id"], "Prefer remote roles",
        conversation_id=conversation["conversation_id"], message_ids=source_ids,
    )
    ranges = service.get_memory_source_context(
        user_id=user["user_id"], memory_id=memory["memory_id"]
    )
    assert 1 <= len(ranges) <= 2
    with pytest.raises(ValueError, match="two"):
        service.get_memory_source_context(
            user_id=user["user_id"], memory_id=memory["memory_id"], max_ranges=3
        )


def test_pending_memory_never_enters_catalog(workspace):
    repository, user, _, _, service = workspace
    repository.create_memory_candidate(
        user["user_id"], category="preference", content="Pending private preference",
        confidence=1, source="conversation",
    )
    assert service.memory_catalog(
        user_id=user["user_id"], query="Find jobs", intent="job_search"
    ) == []


def test_revoked_memory_never_enters_current_catalog(workspace):
    repository, user, _, _, service = workspace
    old = _approve(repository, user["user_id"], "Prefer startups")
    candidate = repository.create_memory_candidate(
        user["user_id"], category="preference", content="Prefer startups",
        confidence=1, source="conversation", operation="REVOKE",
        existing_memory_id=old["memory_id"],
    )
    repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert service.memory_catalog(
        user_id=user["user_id"], query="startup jobs", intent="job_search"
    ) == []


def test_superseded_memory_is_not_current_truth(workspace):
    repository, user, _, _, service = workspace
    old = _approve(repository, user["user_id"], "Prefer startups")
    candidate = repository.create_memory_candidate(
        user["user_id"], category="preference", content="Prefer research labs",
        confidence=1, source="conversation", operation="UPDATE",
        existing_memory_id=old["memory_id"],
    )
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        new = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    cards = service.memory_catalog(
        user_id=user["user_id"], query="Find research jobs", intent="job_search"
    )
    assert {item["memory_id"] for item in cards} == {new["memory_id"]}


def test_irrelevant_memory_is_not_expanded_into_final_context(workspace):
    repository, user, _, conversation, service = workspace
    memory = _approve(repository, user["user_id"], "Prefer healthcare employers")
    result = service.build_context(
        user_id=user["user_id"], conversation_id=conversation["conversation_id"],
        intent="concise_guidance", query="Explain Python decorators",
    )
    assert memory["memory_id"] not in {item["memory_id"] for item in result["memory_details"]}


def test_current_conversation_overlay_overrides_stale_persistent_memory(workspace):
    repository, user, _, conversation, service = workspace
    stale = _approve(repository, user["user_id"], "Prefer startups")
    message = repository.add_message(
        user["user_id"], conversation["conversation_id"], "user", "I prefer large AI labs."
    )
    repository.record_conversation_memory_signals(
        user["user_id"], conversation["conversation_id"], message["message_id"],
        [{"type": "memory.preference", "operation_hint": "replace", "value_hint": ["large AI labs"]}],
    )
    result = service.build_context(
        user_id=user["user_id"], conversation_id=conversation["conversation_id"],
        intent="job_search", query="Find AI jobs",
    )
    assert stale["memory_id"] not in {item["memory_id"] for item in result["memory_details"]}
    assert result["current_conversation_overlay"]["current_thread_memories"]["memory.preference"] == ["large AI labs"]


def test_personalization_references_include_only_loaded_items(workspace):
    repository, user, profile, conversation, service = workspace
    loaded = _approve(repository, user["user_id"], "Prefer remote jobs")
    _approve(repository, user["user_id"], "Prefer healthcare employers")
    result = service.build_context(
        user_id=user["user_id"], conversation_id=conversation["conversation_id"],
        intent="job_search", query="Find remote jobs",
    )
    references = result["personalization_references"]
    assert {item["memory_id"] for item in references["approved_memories"]} == {loaded["memory_id"]}
    assert all(item["profile_version_id"] == profile["profile_version_id"] for item in references["profile"])


def test_personalization_references_do_not_expose_raw_prompt(workspace):
    _, user, _, conversation, service = workspace
    raw_prompt = "SECRET RAW PROMPT MARKER"
    result = service.build_context(
        user_id=user["user_id"], conversation_id=conversation["conversation_id"],
        intent="concise_guidance", query=raw_prompt,
    )
    assert raw_prompt not in json.dumps(result["personalization_references"])


def test_my_profile_has_pending_updates_and_field_history():
    source = inspect.getsource(dashboard._render_profile)
    assert "Current Profile" in source
    assert "Pending Profile Updates" in source
    assert "Field History" in source
    assert "_render_pending_profile_updates" in source


def test_memory_universe_contains_durable_layers_and_review():
    source = inspect.getsource(dashboard._render_memory)
    assert "Semantic memory" in source
    assert "Episodic career memory" in source
    assert "Memory review" in source
    assert "list_semantic_memories" in source
    assert "list_career_events" in source
    assert "list_profile_revision_drafts" in source
    assert "list_conversations" not in source


def test_memory_universe_overview_is_static_and_softly_styled():
    source = inspect.getsource(dashboard._render_memory_universe_overview)
    assert "linear-gradient" in source
    assert "border-radius" in source
    assert "ct-orbit" in source
    assert "unsafe_allow_javascript" not in source


def test_career_analysis_not_present_in_memory_or_profile_ui():
    source = inspect.getsource(dashboard._render_memory) + inspect.getsource(dashboard._render_profile)
    assert "career analysis" not in source.casefold()


def test_career_assistant_owns_conversation_history():
    assistant = inspect.getsource(dashboard._render_career_assistant)
    memory = inspect.getsource(dashboard._render_memory)
    assert "Previous Conversations" in assistant
    assert "Continue conversation" in assistant
    assert "New conversation" in assistant
    assert "list_conversations" in assistant
    assert "list_conversations" not in memory
