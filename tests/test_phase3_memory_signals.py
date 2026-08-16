from __future__ import annotations

from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.graph.career_agent_graph import _fallback_intent, parse_structured_job_request
from app.services.context_manager import ContextManager
from app.services.memory_signals import detect_memory_signals
from app.services.skill_registry import SkillRegistry
from app.state.agent_schema import CareerIntent


def _repository():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    return engine, ProfileRepository(create_session_factory(engine))


def _record(repository, user_id, conversation_id, text):
    message = repository.add_message(user_id, conversation_id, "user", text)
    signals = [item.model_dump(mode="json") for item in detect_memory_signals(text)]
    repository.record_conversation_memory_signals(
        user_id, conversation_id, message["message_id"], signals
    )
    return message, signals


def test_task_only_message_produces_no_memory_signal():
    assert detect_memory_signals("Find internships for me.") == []


def test_profile_skill_statement_produces_profile_signal():
    signals = detect_memory_signals("I also know Rust.")
    assert [(item.type, item.operation_hint, item.value_hint) for item in signals] == [
        ("profile.skills", "add", ["Rust"])
    ]


def test_preference_statement_produces_memory_preference_signal():
    signals = detect_memory_signals("I prefer smaller AI startups.")
    assert signals[0].type == "memory.preference"
    assert signals[0].value_hint == ["smaller AI startups"]


def test_event_statement_produces_event_signal():
    signals = detect_memory_signals("I recently moved to Seattle.")
    assert signals[0].type == "memory.event"
    assert signals[0].value_hint == ["moved to Seattle"]


def test_mixed_job_request_and_preference_preserves_job_intent_and_memory_signal():
    decision = _fallback_intent(
        "I prefer smaller AI startups; find internships for me."
    )
    assert decision.intent == CareerIntent.JOB_SEARCH
    assert decision.memory_worthy is True
    assert decision.memory_signals[0].type == "memory.preference"


def test_current_thread_profile_overlay_affects_job_search():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    repository.upsert_profile(
        user["user_id"],
        {"school": "Example", "major": "CS", "graduation_year": 2028, "skills": ["Python"], "experience": [{"role": "Intern"}]},
    )
    conversation = repository.create_conversation(user["user_id"], "Overlay")
    _record(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    effective = repository.get_effective_conversation_context(
        user["user_id"], conversation["conversation_id"]
    )["effective_profile"]
    request = parse_structured_job_request(
        "Find 5 AI engineering internships in California.", effective
    )
    assert request is not None
    assert request.profile_skills == ["Python", "Rust"]
    engine.dispose()


def test_current_thread_overlay_does_not_modify_persisted_profile():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    repository.upsert_profile(
        user["user_id"],
        {"school": "Example", "major": "CS", "graduation_year": 2028, "skills": ["Python"], "experience": [{"role": "Intern"}]},
    )
    conversation = repository.create_conversation(user["user_id"], "Overlay")
    _record(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    assert repository.get_profile(user["user_id"])["skills"] == ["Python"]
    engine.dispose()


def test_latest_current_thread_statement_overrides_old_approved_memory():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    candidate = repository.create_memory_candidate(
        user["user_id"], category="preference", content="Prefers startups", confidence=1.0, source="conversation"
    )
    repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    conversation = repository.create_conversation(user["user_id"], "Precedence")
    _record(
        repository,
        user["user_id"],
        conversation["conversation_id"],
        "I no longer prefer startups; I prefer large AI research labs.",
    )
    overlay = repository.get_effective_conversation_context(
        user["user_id"], conversation["conversation_id"]
    )["current_thread_memories"]
    assert overlay["memory.preference"] == ["large AI research labs"]
    engine.dispose()


def test_current_thread_signal_does_not_leak_to_another_conversation():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    first = repository.create_conversation(user["user_id"], "First")
    second = repository.create_conversation(user["user_id"], "Second")
    _record(repository, user["user_id"], first["conversation_id"], "I also know Rust.")
    assert repository.get_effective_conversation_context(
        user["user_id"], first["conversation_id"]
    )["effective_profile"]["skills"] == ["Rust"]
    assert repository.get_effective_conversation_context(
        user["user_id"], second["conversation_id"]
    )["effective_profile"].get("skills") in (None, [])
    engine.dispose()


def test_signal_bearing_turn_is_preserved_by_context_compression():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    conversation = repository.create_conversation(user["user_id"], "Compression")
    _record(repository, user["user_id"], conversation["conversation_id"], "I also know Rust.")
    for index in range(8):
        repository.add_message(
            user["user_id"], conversation["conversation_id"],
            "assistant" if index % 2 else "user", f"ordinary {index} " + "x" * 1000,
        )
    manager = ContextManager(repository, SkillRegistry())
    with patch.object(manager, "compression_threshold", return_value=1000), patch(
        "app.services.context_manager.get_llm", side_effect=RuntimeError("offline")
    ):
        messages = manager.build_messages(
            user_id=user["user_id"], conversation_id=conversation["conversation_id"],
            current_request="What next?", agent_status={},
        )
    assert "Rust" in "\n".join(str(item.content) for item in messages)
    engine.dispose()


def test_memory_worthy_signal_marks_conversation_extraction_pending():
    engine, repository = _repository()
    user = repository.get_or_create_user("Ada")
    conversation = repository.create_conversation(user["user_id"], "Pending")
    message, signals = _record(
        repository, user["user_id"], conversation["conversation_id"], "I also know Rust."
    )
    assert signals
    state = repository.get_conversation_memory_state(
        user["user_id"], conversation["conversation_id"]
    )
    assert state["pending"] is True
    assert state["pending_boundary_message_id"] == message["message_id"]
    engine.dispose()
