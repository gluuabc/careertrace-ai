from __future__ import annotations

import pytest

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.services.conversation_memory import (
    ConversationMemoryExtractor,
    ExtractedMemoryProposal,
    MemoryExtractionOutput,
    recover_pending_conversation_extractions,
    trigger_conversation_boundary,
)
from app.services.memory_signals import detect_memory_signals, merge_memory_signals
from app.state.agent_schema import MemorySignal


class FalseGoalModel:
    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return MemoryExtractionOutput(
            proposals=[
                ExtractedMemoryProposal(
                    destination="memory",
                    category="goal",
                    operation="replace",
                    values=[
                        "ML Engineer",
                        "Software Engineer",
                        "Data Scientist",
                    ],
                    source_message_ids=[],
                )
            ]
        )


class UnsupportedProfileFactModel:
    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return MemoryExtractionOutput(
            proposals=[
                ExtractedMemoryProposal(
                    destination="profile",
                    category="profile_fact",
                    field_key="school",
                    operation="replace",
                    values=["Example University"],
                    source_message_ids=[],
                )
            ]
        )


@pytest.fixture
def memory_workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Memory Student", "memory-phase8@example.com")
    conversation = repository.create_conversation(user["user_id"], "Memory")
    yield repository, user, conversation
    engine.dispose()


@pytest.mark.parametrize(
    "prompt",
    [
        "Which role fits me better: ML Engineer, Software Engineer, or Data Scientist?",
        "Should I focus on ML or software engineering?",
    ],
)
def test_advisory_comparisons_have_no_goal_signal(prompt):
    assert [item for item in detect_memory_signals(prompt) if item.type == "memory.goal"] == []


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("My career goal is ML engineering.", ["ML engineering"]),
        (
            "I want to target ML engineering roles going forward.",
            ["ML engineering roles"],
        ),
    ],
)
def test_explicit_durable_goal_statements_are_retained(prompt, expected):
    goals = [item for item in detect_memory_signals(prompt) if item.type == "memory.goal"]
    assert len(goals) == 1
    assert goals[0].value_hint == expected


def test_mixed_advisory_message_retains_only_explicit_goal():
    prompt = (
        "My goal is ML engineering. Which role fits me better, ML or data science?"
    )
    goals = [item for item in detect_memory_signals(prompt) if item.type == "memory.goal"]

    assert len(goals) == 1
    assert goals[0].value_hint == ["ML engineering"]


def test_classifier_proposed_goal_requires_deterministic_support():
    classified = [
        MemorySignal(
            type="memory.goal",
            operation_hint="add",
            value_hint=["ML Engineer", "Software Engineer", "Data Scientist"],
        )
    ]

    assert merge_memory_signals(classified, []) == []
    explicit = detect_memory_signals("My career goal is ML engineering.")
    assert merge_memory_signals(classified, explicit) == explicit


def test_classifier_proposed_non_goal_fact_requires_deterministic_support():
    classified = [
        MemorySignal(
            type="profile.school",
            operation_hint="replace",
            value_hint=["Example University"],
        )
    ]

    assert merge_memory_signals(classified, []) == []
    explicit = detect_memory_signals("I attend Example University.")
    assert merge_memory_signals(classified, explicit) == explicit


def test_extractor_rejects_false_classifier_goal_for_advisory_question(
    memory_workspace,
):
    repository, user, conversation = memory_workspace
    repository.add_message(
        user["user_id"],
        conversation["conversation_id"],
        "user",
        "Which role fits me better: ML Engineer, Software Engineer, or Data Scientist?",
    )
    repository.mark_conversation_extraction_pending(
        user["user_id"], conversation["conversation_id"]
    )

    ConversationMemoryExtractor(
        repository, lambda _kind: FalseGoalModel()
    ).extract(user["user_id"], conversation["conversation_id"])

    assert repository.list_memory_candidates(user["user_id"]) == []


def test_extractor_rejects_unsupported_profile_fact_from_search_question(
    memory_workspace,
):
    repository, user, conversation = memory_workspace
    repository.add_message(
        user["user_id"],
        conversation["conversation_id"],
        "user",
        "Find alumni from Example University working in robotics.",
    )
    repository.mark_conversation_extraction_pending(
        user["user_id"], conversation["conversation_id"]
    )

    ConversationMemoryExtractor(
        repository, lambda _kind: UnsupportedProfileFactModel()
    ).extract(user["user_id"], conversation["conversation_id"])

    assert repository.list_profile_revision_drafts(user["user_id"]) == []


def test_explicit_goal_survives_classifier_and_recovery_exactly_once(
    memory_workspace,
):
    repository, user, conversation = memory_workspace
    message = repository.add_message(
        user["user_id"],
        conversation["conversation_id"],
        "user",
        "My career goal is ML engineering.",
    )
    signals = [
        item.model_dump(mode="json")
        for item in detect_memory_signals(message["content"])
    ]
    repository.record_conversation_memory_signals(
        user["user_id"], conversation["conversation_id"], message["message_id"], signals
    )
    trigger_conversation_boundary(
        user["user_id"],
        conversation["conversation_id"],
        process_now=False,
        repository=repository,
    )

    first = recover_pending_conversation_extractions(
        user["user_id"], repository, lambda _kind: FalseGoalModel()
    )
    second = recover_pending_conversation_extractions(
        user["user_id"], repository, lambda _kind: FalseGoalModel()
    )
    candidates = repository.list_memory_candidates(user["user_id"])

    assert first[0]["status"] == "completed"
    assert second == []
    assert len(candidates) == 1
    assert candidates[0]["category"] == "goal"
    assert candidates[0]["content"] == "ML engineering"
