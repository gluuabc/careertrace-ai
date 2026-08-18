from __future__ import annotations

from unittest.mock import patch

import pytest

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.retrieval_repository import RetrievalRepository
from app.database.repository import ProfileRepository
from app.services.conversation_memory import (
    ConversationMemoryExtractor,
    build_memory_extraction_input,
    recover_pending_conversation_extractions,
    trigger_conversation_boundary,
)
from app.services.memory_signals import detect_memory_signals


def _offline(_kind):
    raise RuntimeError("offline")


class _StaticExtractionModel:
    def __init__(self, proposals):
        self.proposals = proposals

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return {"proposals": self.proposals}


@pytest.fixture
def workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Ada")
    profile = repository.upsert_profile(
        user["user_id"],
        {"school": "Example", "major": "CS", "graduation_year": 2028, "skills": ["Python"], "experience": [{"role": "Intern"}]},
    )
    conversation = repository.create_conversation(user["user_id"], "Memory")
    yield repository, user, profile, conversation
    engine.dispose()


def _signal_message(repository, user_id, conversation_id, text, signals=None):
    message = repository.add_message(user_id, conversation_id, "user", text)
    values = signals or [item.model_dump(mode="json") for item in detect_memory_signals(text)]
    repository.record_conversation_memory_signals(user_id, conversation_id, message["message_id"], values)
    return message


def _extract(repository, user_id, conversation_id):
    repository.mark_conversation_extraction_pending(user_id, conversation_id)
    return ConversationMemoryExtractor(repository, _offline).extract(user_id, conversation_id)


def _approved(repository, user_id, category, content):
    candidate = repository.create_memory_candidate(user_id, category=category, content=content, confidence=1.0, source="test")
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        return repository.review_memory_candidate(user_id, candidate["candidate_id"], accept=True)


def _pending(repository, user_id):
    return [item for item in repository.list_memory_candidates(user_id) if item["status"] == "pending"]


def _make_long_segment(repository, user_id, conversation_id, marked_type="profile.skills", operation="add", value="Rust"):
    marked = None
    ids = []
    for index in range(18):
        role = "user" if index % 2 == 0 else "assistant"
        message = repository.add_message(user_id, conversation_id, role, f"turn {index} " + "x" * 500)
        ids.append(message["message_id"])
        if index == 8:
            marked = message
            repository.record_conversation_memory_signals(
                user_id, conversation_id, message["message_id"],
                [{"type": marked_type, "operation_hint": operation, "value_hint": [value]}],
            )
    repository.mark_conversation_extraction_pending(user_id, conversation_id, ids[-1])
    return ids, marked


def test_short_unprocessed_segment_uses_entire_original_segment(workspace):
    repository, user, _, conversation = workspace
    first = _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    second = repository.add_message(user["user_id"], conversation["conversation_id"], "assistant", "Noted")
    repository.mark_conversation_extraction_pending(user["user_id"], conversation["conversation_id"])
    payload = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])
    assert payload["mode"] == "entire_original_segment"
    assert [item["message_id"] for item in payload["messages"]] == [first["message_id"], second["message_id"]]


def test_long_segment_prioritizes_all_user_context(workspace):
    repository, user, _, conversation = workspace
    ids, _ = _make_long_segment(repository, user["user_id"], conversation["conversation_id"])
    payload = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])
    assert payload["mode"] == "user_prioritized_context"
    selected = {item["message_id"] for item in payload["messages"]}
    assert set(ids[::2]).issubset(selected)


def test_profile_semantic_signal_uses_one_exchange_surrounding_context(workspace):
    repository, user, _, conversation = workspace
    ids, marked = _make_long_segment(repository, user["user_id"], conversation["conversation_id"])
    selected = {item["message_id"] for item in build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])["messages"]}
    center = ids.index(marked["message_id"])
    assert set(ids[center - 2:center + 3]).issubset(selected)


def test_event_signal_uses_two_exchange_surrounding_context(workspace):
    repository, user, _, conversation = workspace
    ids, marked = _make_long_segment(repository, user["user_id"], conversation["conversation_id"], "memory.event", "add", "accepted an offer yesterday")
    selected = {item["message_id"] for item in build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])["messages"]}
    center = ids.index(marked["message_id"])
    assert set(ids[center - 4:center + 5]).issubset(selected)


def test_same_type_signals_are_grouped_chronologically(workspace):
    repository, user, _, conversation = workspace
    first = _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    second = _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer large AI labs.")
    repository.mark_conversation_extraction_pending(user["user_id"], conversation["conversation_id"])
    grouped = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])["signals_grouped_chronologically"]["memory.preference"]
    assert [item["source_message_id"] for item in grouped] == [first["message_id"], second["message_id"]]


def test_extractor_never_reads_pre_watermark_messages_as_new_candidate_source(workspace):
    repository, user, _, conversation = workspace
    old = _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    new = _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Go.")
    repository.mark_conversation_extraction_pending(user["user_id"], conversation["conversation_id"])
    payload = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])
    assert old["message_id"] not in {item["message_id"] for item in payload["messages"]}
    assert new["message_id"] in {item["message_id"] for item in payload["messages"]}


def test_extraction_input_respects_6000_token_budget(workspace):
    repository, user, _, conversation = workspace
    _make_long_segment(repository, user["user_id"], conversation["conversation_id"])
    payload = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])
    assert payload["estimated_input_tokens"] <= 6000


def test_marked_user_statement_survives_extraction_trimming(workspace):
    repository, user, _, conversation = workspace
    _, marked = _make_long_segment(repository, user["user_id"], conversation["conversation_id"])
    payload = build_memory_extraction_input(repository, user["user_id"], conversation["conversation_id"])
    assert marked["message_id"] in {item["message_id"] for item in payload["messages"]}


@pytest.mark.parametrize("reason", ["switch", "new_conversation"])
def test_switch_or_new_conversation_triggers_pending_extraction(workspace, reason):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    result = trigger_conversation_boundary(user["user_id"], conversation["conversation_id"], process_now=True, repository=repository, model_factory=_offline)
    assert result["status"] == "completed"


def test_switch_conversation_triggers_pending_extraction(workspace):
    test_switch_or_new_conversation_triggers_pending_extraction(workspace, "switch")


def test_new_conversation_triggers_pending_extraction(workspace):
    test_switch_or_new_conversation_triggers_pending_extraction(workspace, "new_conversation")


def test_logout_marks_pending_without_blocking_on_extraction(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    state = trigger_conversation_boundary(user["user_id"], conversation["conversation_id"], process_now=False, repository=repository, model_factory=lambda _: (_ for _ in ()).throw(AssertionError("model called")))
    assert state["pending"] is True


def test_boundary_creates_extraction_state_without_regex_signals(workspace):
    repository, user, _, conversation = workspace
    text = "Rust has become central to my toolkit."
    assert detect_memory_signals(text) == []
    message = repository.add_message(
        user["user_id"], conversation["conversation_id"], "user", text
    )

    state = trigger_conversation_boundary(
        user["user_id"],
        conversation["conversation_id"],
        process_now=False,
        repository=repository,
    )

    assert state["pending"] is True
    assert state["pending_boundary_message_id"] == message["message_id"]


def test_natural_unmatched_turn_can_create_all_review_candidate_types(workspace):
    repository, user, _, conversation = workspace
    text = (
        "Rust has become central to my toolkit. "
        "Remote-first teams suit how I work. "
        "Applying for machine learning internships next semester is my plan."
    )
    assert detect_memory_signals(text) == []
    message = repository.add_message(
        user["user_id"], conversation["conversation_id"], "user", text
    )

    def proposal(destination, evidence, **values):
        start = text.index(evidence)
        return {
            "destination": destination,
            "source_message_id": message["message_id"],
            "evidence_text": evidence,
            "evidence_start": start,
            "evidence_end": start + len(evidence),
            "self_referential": True,
            "confidence": 0.9,
            **values,
        }

    proposals = [
        proposal(
            "profile",
            "Rust has become central to my toolkit.",
            profile_field="skills",
            value=["Rust"],
        ),
        proposal(
            "semantic_memory",
            "Remote-first teams suit how I work.",
            semantic_group="work_style",
            topic_key="work_mode",
            value="remote-first teams",
        ),
        proposal(
            "episodic_memory",
            "Applying for machine learning internships next semester is my plan.",
            content="Apply for machine learning internships next semester",
            event_status="planned",
        ),
    ]
    model = _StaticExtractionModel(proposals)

    result = trigger_conversation_boundary(
        user["user_id"],
        conversation["conversation_id"],
        process_now=True,
        repository=repository,
        model_factory=lambda _kind: model,
    )

    assert result["status"] == "completed"
    drafts = repository.list_profile_revision_drafts(user["user_id"])
    assert drafts[0]["changes"][0]["field_key"] == "skills"
    candidates = _pending(repository, user["user_id"])
    assert {item["memory_kind"] for item in candidates} == {"semantic", "episodic"}


def test_next_login_recovers_pending_extraction(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    trigger_conversation_boundary(user["user_id"], conversation["conversation_id"], process_now=False, repository=repository)
    results = recover_pending_conversation_extractions(user["user_id"], repository, _offline)
    assert results[0]["status"] == "completed"


def test_extraction_watermark_prevents_duplicate_candidates(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    count = len(repository.list_memory_candidates(user["user_id"]))
    repository.mark_conversation_extraction_pending(user["user_id"], conversation["conversation_id"])
    ConversationMemoryExtractor(repository, _offline).extract(user["user_id"], conversation["conversation_id"])
    assert len(repository.list_memory_candidates(user["user_id"])) == count
    state = repository.get_conversation_memory_state(
        user["user_id"], conversation["conversation_id"]
    )
    assert state["pending"] is False


def test_failed_extraction_does_not_advance_watermark(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    repository.mark_conversation_extraction_pending(user["user_id"], conversation["conversation_id"])
    extractor = ConversationMemoryExtractor(repository, _offline)
    with patch.object(extractor, "_persist_proposals", side_effect=RuntimeError("fail")), pytest.raises(RuntimeError):
        extractor.extract(user["user_id"], conversation["conversation_id"])
    state = repository.get_conversation_memory_state(user["user_id"], conversation["conversation_id"])
    assert state["last_memory_extraction_message_id"] is None
    assert state["pending"] is True


def test_profile_signal_routes_to_profile_revision_draft(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert repository.list_profile_revision_drafts(user["user_id"])[0]["changes"][0]["field_key"] == "skills"


def test_profile_candidate_is_not_saved_as_flexible_memory(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert [item for item in repository.list_memory_candidates(user["user_id"]) if item["status"] == "pending"] == []


def test_memory_preference_routes_to_memory_candidate(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert repository.list_memory_candidates(user["user_id"])[0]["category"] == "preference"


def test_event_memory_preserves_event_time_separate_from_created_at(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I recently accepted an offer yesterday.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    candidate = _pending(repository, user["user_id"])[0]
    assert candidate["event_time"] is not None
    assert candidate["raw_temporal_expression"] == "yesterday"
    assert candidate["event_time"] != candidate["created_at"]


def test_unknown_event_time_remains_null(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I recently accepted an offer.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert repository.list_memory_candidates(user["user_id"])[0]["event_time"] is None


def test_resume_revision_draft_and_profile_revision_draft_are_independent(workspace):
    repository, user, profile, conversation = workspace
    resume = repository.save_resume_revision_draft(user["user_id"], {"source_profile_version_id": profile["profile_version_id"], "summary": "Resume wording", "changes": [{"section": "skills", "proposed_text": "Python and Rust", "rationale": "Target role"}]})
    profile_draft = repository.create_profile_revision_draft(user["user_id"], source_type="conversation", source_conversation_id=conversation["conversation_id"], source_message_ids=[], changes=[{"field_key": "skills", "operation": "add", "before_value": ["Python"], "proposed_value": ["Python", "Rust"]}])
    assert resume["draft_id"] != profile_draft["draft_id"]
    assert repository.list_resume_revision_drafts(user["user_id"])[0]["status"] == "draft"


def test_profile_draft_review_is_field_scoped(workspace):
    repository, user, _, conversation = workspace
    draft = repository.create_profile_revision_draft(user["user_id"], source_type="conversation", source_conversation_id=conversation["conversation_id"], source_message_ids=[], changes=[{"field_key": "skills", "operation": "add", "before_value": ["Python"], "proposed_value": ["Python", "Rust"]}, {"field_key": "major", "operation": "replace", "before_value": "CS", "proposed_value": "AI"}])
    first, second = draft["changes"]
    repository.review_profile_revision_change(user["user_id"], first["change_id"], accept=True)
    refreshed = repository.list_profile_revision_drafts(user["user_id"])[0]
    assert [item["status"] for item in refreshed["changes"]] == ["accepted", "pending"]


def test_unrelated_field_change_does_not_stale_profile_draft_change(workspace):
    repository, user, _, conversation = workspace
    draft = repository.create_profile_revision_draft(user["user_id"], source_type="conversation", source_conversation_id=conversation["conversation_id"], source_message_ids=[], changes=[{"field_key": "skills", "operation": "add", "before_value": ["Python"], "proposed_value": ["Python", "Rust"]}])
    repository.apply_profile_field_changes(user["user_id"], {"major": "AI"}, source_type="manual")
    reviewed = repository.review_profile_revision_change(user["user_id"], draft["changes"][0]["change_id"], accept=True)
    assert reviewed["status"] == "accepted"


def test_same_field_change_stales_profile_draft_change(workspace):
    repository, user, _, conversation = workspace
    draft = repository.create_profile_revision_draft(user["user_id"], source_type="conversation", source_conversation_id=conversation["conversation_id"], source_message_ids=[], changes=[{"field_key": "skills", "operation": "add", "before_value": ["Python"], "proposed_value": ["Python", "Rust"]}])
    repository.apply_profile_field_changes(user["user_id"], {"skills": ["Python", "Go"]}, source_type="manual")
    reviewed = repository.review_profile_revision_change(user["user_id"], draft["changes"][0]["change_id"], accept=True)
    assert reviewed["status"] == "stale"


def test_memory_add_candidate(workspace):
    repository, user, _, conversation = workspace
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert repository.list_memory_candidates(user["user_id"])[0]["operation"] == "ADD"


def test_memory_duplicate_becomes_noop(workspace):
    repository, user, _, conversation = workspace
    _approved(repository, user["user_id"], "preference", "startups")
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer startups.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert [item for item in repository.list_memory_candidates(user["user_id"]) if item["status"] == "pending"] == []


def test_unclassified_deterministic_correction_is_non_destructive(workspace):
    repository, user, _, conversation = workspace
    old = _approved(repository, user["user_id"], "preference", "startups")
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I no longer prefer startups; I prefer large AI labs.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    candidate = _pending(repository, user["user_id"])[0]
    assert candidate["operation"] == "ADD" and candidate["existing_memory_id"] is None
    assert old["active"] is True


def test_unclassified_deterministic_revocation_does_not_revoke(workspace):
    repository, user, _, conversation = workspace
    _approved(repository, user["user_id"], "preference", "startups")
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I no longer prefer startups.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert _pending(repository, user["user_id"]) == []
    assert repository.list_memories(user["user_id"])[0]["active"] is True


def test_same_group_different_topic_defaults_to_add(workspace):
    repository, user, _, conversation = workspace
    _approved(repository, user["user_id"], "preference", "startups")
    _signal_message(repository, user["user_id"], conversation["conversation_id"], "I prefer healthcare roles.")
    _extract(repository, user["user_id"], conversation["conversation_id"])
    assert _pending(repository, user["user_id"])[0]["operation"] == "ADD"


def test_rejected_memory_never_enters_retrieval(workspace):
    repository, user, _, _ = workspace
    candidate = repository.create_memory_candidate(user["user_id"], category="goal", content="Become a CTO", confidence=1, source="test")
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory") as index:
        repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=False)
    index.assert_not_called()
    assert repository.list_memories(user["user_id"]) == []


def test_approved_candidate_moves_to_approved_memory(workspace):
    repository, user, _, _ = workspace
    candidate = repository.create_memory_candidate(user["user_id"], category="goal", content="Become a CTO", confidence=1, source="test")
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        memory = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert memory["active"] is True and memory["retrieval_index_status"] == "synced"


def test_superseded_memory_is_inactive_but_not_deleted(workspace):
    repository, user, _, _ = workspace
    old = _approved(repository, user["user_id"], "preference", "startups")
    candidate = repository.create_memory_candidate(user["user_id"], category="preference", content="large labs", confidence=1, source="test", operation="UPDATE", existing_memory_id=old["memory_id"])
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        new = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    all_memories = repository.list_memories(user["user_id"], include_inactive=True)
    prior = next(item for item in all_memories if item["memory_id"] == old["memory_id"])
    assert prior["active"] is False
    assert new["supersedes_memory_id"] == old["memory_id"]


def test_approved_memory_index_failure_is_not_silently_ignored(workspace):
    repository, user, _, _ = workspace
    candidate = repository.create_memory_candidate(user["user_id"], category="goal", content="Become a CTO", confidence=1, source="test")
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", side_effect=RuntimeError("index failed")):
        memory = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert memory["retrieval_index_status"] == "failed"
    assert memory["retrieval_index_error"] == "RuntimeError"


def test_memory_candidate_rejects_another_users_provenance(workspace):
    repository, user, _, _ = workspace
    other = repository.get_or_create_user("Grace")
    other_conversation = repository.create_conversation(other["user_id"], "Private")
    other_message = repository.add_message(
        other["user_id"], other_conversation["conversation_id"], "user", "Private fact"
    )

    with pytest.raises(ValueError, match="not available|not found"):
        repository.create_memory_candidate(
            user["user_id"],
            category="preference",
            content="Private fact",
            confidence=1,
            source="conversation",
            source_conversation_id=other_conversation["conversation_id"],
            source_message_ids=[other_message["message_id"]],
        )


def test_superseding_memory_deactivates_old_retrieval_chunks(workspace):
    repository, user, _, _ = workspace
    old = _approved(repository, user["user_id"], "preference", "startups")
    retrieval = RetrievalRepository(repository.session_factory)
    retrieval.upsert_document(
        corpus_type="approved_memory",
        user_id=user["user_id"],
        source_entity_id=f"{old['memory_id']}:chunk:0",
        source_version="1",
        title="preference",
        text_content="startups",
    )
    candidate = repository.create_memory_candidate(
        user["user_id"],
        category="preference",
        content="large labs",
        confidence=1,
        source="test",
        operation="UPDATE",
        existing_memory_id=old["memory_id"],
    )
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)

    assert retrieval.has_documents(user["user_id"], ["approved_memory"]) is False
