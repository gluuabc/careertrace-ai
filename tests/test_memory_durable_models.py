from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository


def _repository():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Memory Owner")
    return engine, repository, user


def test_approved_semantic_candidate_materializes_separate_model():
    engine, repository, user = _repository()
    candidate = repository.create_memory_candidate(
        user["user_id"], category="motivation", content="building useful tools",
        confidence=.8, source="test", memory_kind="semantic",
        semantic_group="motivation", topic_key="useful_technology",
        proposed_value="building useful tools", evidence_text="I am motivated by building useful tools.",
    )
    result = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert result["memory_kind"] == "semantic"
    assert repository.list_memories(user["user_id"]) == []
    assert repository.list_semantic_memories(user["user_id"])[0]["semantic_group"] == "motivation"
    engine.dispose()


def test_approved_event_preserves_unknown_optional_fields():
    engine, repository, user = _repository()
    candidate = repository.create_memory_candidate(
        user["user_id"], category="event", content="working on CareerTrace",
        confidence=.7, source="test", memory_kind="episodic", event_status="current",
        evidence_text="I am currently working on CareerTrace.",
    )
    result = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert result["event_status"] == "current"
    assert result["title"] is result["outcome"] is None
    engine.dispose()


def test_semantic_storage_is_user_scoped():
    engine, repository, user = _repository()
    other = repository.get_or_create_user("Other")
    candidate = repository.create_memory_candidate(
        user["user_id"], category="value", content="accessibility", confidence=None,
        source="test", memory_kind="semantic", semantic_group="value", topic_key="product_values",
        proposed_value="accessibility",
    )
    repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert repository.list_semantic_memories(other["user_id"]) == []
    engine.dispose()
