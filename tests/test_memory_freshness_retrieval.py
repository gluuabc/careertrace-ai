from types import SimpleNamespace
from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.database.retrieval_repository import RetrievalRepository
from app.services.memory_retrieval import ProgressiveMemoryService


def _workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Fresh")
    repository.upsert_profile(user["user_id"], {
        "school": "Example", "major": "Computer Science", "graduation_year": 2028,
        "skills": ["Python"], "experience": [{"role": "Intern"}],
    })
    conversation = repository.create_conversation(user["user_id"], "Existing")
    return engine, repository, user, conversation


def test_later_profile_save_beats_stale_same_conversation_overlay():
    engine, repository, user, conversation = _workspace()
    message = repository.add_message(user["user_id"], conversation["conversation_id"], "user", "My major is AI.")
    repository.record_conversation_memory_signals(user["user_id"], conversation["conversation_id"], message["message_id"], [
        {"type": "profile.major", "operation_hint": "replace", "value_hint": ["AI"]}
    ])
    repository.apply_profile_field_changes(user["user_id"], {"major": "Data Science"}, source_type="manual")
    effective = repository.get_effective_conversation_context(user["user_id"], conversation["conversation_id"])
    assert effective["effective_profile"]["major"] == "Data Science"
    engine.dispose()


def test_approved_profile_revision_is_immediately_visible_in_same_conversation():
    engine, repository, user, conversation = _workspace()
    draft = repository.create_profile_revision_draft(
        user["user_id"], source_type="conversation", source_conversation_id=conversation["conversation_id"],
        source_message_ids=[], changes=[{"field_key": "major", "operation": "update", "before_value": "Computer Science", "proposed_value": "AI"}],
    )
    repository.review_profile_revision_change(user["user_id"], draft["changes"][0]["change_id"], accept=True)
    repository.apply_profile_revision_draft(user["user_id"], draft["draft_id"])
    assert repository.get_effective_conversation_context(user["user_id"], conversation["conversation_id"])["effective_profile"]["major"] == "AI"
    engine.dispose()


def test_structured_approval_synchronizes_retrieval_document():
    engine, repository, user, _ = _workspace()
    retrieval = RetrievalRepository(repository.session_factory)
    candidate = repository.create_memory_candidate(
        user["user_id"], category="interest", content="NLP research", confidence=.8,
        source="test", memory_kind="semantic", semantic_group="interest", topic_key="nlp_research",
        proposed_value="NLP research",
    )
    def sparse_index(_self, *, user_id, memory):
        return [retrieval.upsert_document(
            corpus_type="semantic_memory", user_id=user_id,
            source_entity_id=f"{memory['semantic_memory_id']}:chunk:0", source_version="1",
            title="interest: nlp_research", text_content=str(memory["value"]),
            metadata={"semantic_memory_id": memory["semantic_memory_id"]},
        )]
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_semantic_memory", new=sparse_index):
        approved = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    assert approved["retrieval_index_status"] == "synced"
    hits = retrieval.sparse_search(user["user_id"], "NLP research", ["semantic_memory"])
    assert hits[0][0]["metadata"]["semantic_memory_id"] == approved["semantic_memory_id"]
    engine.dispose()


class _CaptureRetrieval:
    def __init__(self):
        self.calls = []
    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(items=[])


def test_general_and_explicit_queries_choose_eligible_corpora_before_retrieval():
    engine, repository, user, _ = _workspace()
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_semantic_memory", return_value=[]):
        semantic = repository.create_memory_candidate(user["user_id"], category="value", content="impact", confidence=None, source="test", memory_kind="semantic", semantic_group="value", topic_key="career_impact", proposed_value="impact")
        repository.review_memory_candidate(user["user_id"], semantic["candidate_id"], accept=True)
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_career_event", return_value=[]):
        event = repository.create_memory_candidate(user["user_id"], category="event", content="built CareerTrace", confidence=None, source="test", memory_kind="episodic", event_status="completed")
        repository.review_memory_candidate(user["user_id"], event["candidate_id"], accept=True)
    capture = _CaptureRetrieval()
    service = ProgressiveMemoryService(repository, capture)
    service.memory_catalog(user_id=user["user_id"], query="Which role fits me best?", intent="concise_guidance")
    assert {"semantic_memory", "episodic_event"}.issubset(capture.calls[-1]["corpus_types"])
    service.memory_catalog(user_id=user["user_id"], query="What preferences do you remember?", intent="concise_guidance")
    assert capture.calls[-1]["corpus_types"] == ["semantic_memory"]
    service.memory_catalog(user_id=user["user_id"], query="What did I do last summer?", intent="concise_guidance")
    assert capture.calls[-1]["corpus_types"] == ["episodic_event"]
    engine.dispose()


def test_general_role_fit_context_can_expand_semantic_and_episodic_results():
    engine, repository, user, conversation = _workspace()
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_semantic_memory", return_value=[]):
        candidate = repository.create_memory_candidate(user["user_id"], category="preference", content="research teams", confidence=None, source="test", memory_kind="semantic", semantic_group="preference", topic_key="team_environment", proposed_value="research teams")
        semantic = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_career_event", return_value=[]):
        candidate = repository.create_memory_candidate(user["user_id"], category="event", content="built an NLP evaluator", confidence=None, source="test", memory_kind="episodic", event_status="completed")
        event = repository.review_memory_candidate(user["user_id"], candidate["candidate_id"], accept=True)
    class Ranked:
        def retrieve(self, **_kwargs):
            return SimpleNamespace(items=[
                SimpleNamespace(metadata={"semantic_memory_id": semantic["semantic_memory_id"]}, text_excerpt="research teams"),
                SimpleNamespace(metadata={"career_event_id": event["career_event_id"]}, text_excerpt="built an NLP evaluator"),
            ])
    context = ProgressiveMemoryService(repository, Ranked()).build_context(
        user_id=user["user_id"], conversation_id=conversation["conversation_id"],
        intent="concise_guidance", query="Which role fits me best?",
    )
    assert {item["type"] for item in context["memory_details"]} == {"preference", "event"}
    engine.dispose()
