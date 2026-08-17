from app.services.conversation_memory import (
    ExtractedMemoryProposal,
    _validated_extraction_proposals,
    normalize_topic_key,
)


def _payload(text: str = "I enjoy working with LLM and NLP projects."):
    return {
        "messages": [{"message_id": "m1", "role": "user", "content": text}],
        "signals_grouped_chronologically": {},
    }


def _semantic(text: str, **changes):
    values = dict(
        destination="semantic_memory", source_message_id="m1",
        evidence_text=text, evidence_start=0, evidence_end=len(text),
        confidence=0.8, operation_hint="add", self_referential=True,
        semantic_group="Interest", topic_key="NLP-Research",
        value="enjoys LLM and NLP projects",
    )
    values.update(changes)
    return ExtractedMemoryProposal(**values)


def test_llm_only_exact_grounded_open_group_survives():
    text = "I enjoy working with LLM and NLP projects."
    result = _validated_extraction_proposals(_payload(text), [_semantic(text, semantic_group="creative motivation")])
    assert len(result) == 1
    assert result[0].semantic_group == "creative_motivation"


def test_paraphrased_evidence_and_third_party_are_rejected():
    text = "My friend wants to become a machine learning engineer."
    paraphrase = _semantic(text, evidence_text="I want to become an ML engineer")
    third_party = _semantic(text)
    assert _validated_extraction_proposals(_payload(text), [paraphrase, third_party]) == []


def test_llm_and_deterministic_equivalent_proposal_deduplicates():
    text = "I prefer remote roles."
    payload = _payload(text)
    payload["signals_grouped_chronologically"] = {
        "memory.preference": [{"source_message_id": "m1", "operation_hint": "replace", "value_hint": ["remote roles"]}]
    }
    result = _validated_extraction_proposals(payload, [_semantic(
        text, semantic_group="preference", topic_key="remote-work", value="remote roles"
    )])
    assert len(result) == 1
    assert result[0].topic_key == "work_mode"
    assert result[0].proposal_sources == ["llm", "deterministic"]


def test_topic_normalization_aliases_are_stable():
    assert normalize_topic_key(" Remote-vs onsite ") == "work_mode"
    assert normalize_topic_key("Personal  Values") == "personal_values"


def test_assistant_evidence_never_validates():
    text = "You should prefer remote work."
    payload = _payload(text)
    payload["messages"][0]["role"] = "assistant"
    assert _validated_extraction_proposals(payload, [_semantic(text)]) == []
