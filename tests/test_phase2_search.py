from __future__ import annotations

import hashlib
import time

import pytest

from app.graph.career_agent_graph import parse_structured_job_request
from app.llm.model import resolve_bedrock_model_id
from app.services.demo_search_fixtures import (
    load_demo_search_fixtures,
    should_use_demo_fallback,
)
from app.services.job_search import apply_hard_filters, lexical_job_shortlist
from app.services.retrieval import HybridRetrievalService
from app.services.search_providers import run_provider_fetches
from app.services.search_telemetry import SearchTelemetryRecorder
from app.state.agent_schema import (
    JobCandidate,
    JobSearchRequest,
    PeopleCandidate,
    PeopleSearchRequest,
    RequirementStatus,
    SourceStatus,
)
from app.tools.sources.base import SourceResult


def _job(**updates):
    payload = {
        "candidate_id": "job_1",
        "title": "AI Engineer Intern",
        "company": "Example",
        "location": "California",
        "source_name": "greenhouse",
        "source_url": "https://example.com/jobs/1",
        "application_url": "https://example.com/jobs/1",
    }
    payload.update(updates)
    return JobCandidate.model_validate(payload)


def test_search_tool_records_phase_latency():
    calls = []

    class Repository:
        def create_search_phase_metric(self, user_id, **values):
            calls.append((user_id, values))

    recorder = SearchTelemetryRecorder(
        Repository(), user_id="user", run_id="run", search_session_id="search"
    )
    recorder.observe("normalization", 12, candidate_count=4)
    assert calls[0][1]["duration_ms"] == 12
    assert calls[0][1]["candidate_count"] == 4
    assert "raw_content" not in calls[0][1]


def test_job_default_result_limit_is_five():
    assert JobSearchRequest().requested_count == 5
    assert JobSearchRequest().page_size == 5


def test_job_max_result_limit_is_ten():
    assert JobSearchRequest(requested_count=20, max_results=20).max_results == 10


def test_people_default_result_limit_is_five():
    request = PeopleSearchRequest(person_type="professor")
    assert request.requested_count == 5
    assert request.page_size == 5


def test_people_max_result_limit_is_ten():
    request = PeopleSearchRequest(
        person_type="professor", requested_count=20, max_results=20
    )
    assert request.requested_count == request.max_results == 10


def test_job_source_status_is_separate_from_requirement_status():
    item = apply_hard_filters(_job(source_status=SourceStatus.OFFICIAL_SOURCE), JobSearchRequest())
    assert item.source_status == SourceStatus.OFFICIAL_SOURCE
    assert item.requirement_status == RequirementStatus.REQUIREMENTS_NOT_FULLY_VERIFIED


def test_official_job_with_unknown_requirement_is_not_mislabeled_fake_or_unverified_source():
    item = apply_hard_filters(_job(source_status=SourceStatus.OFFICIAL_SOURCE), JobSearchRequest())
    assert item.source_status == SourceStatus.OFFICIAL_SOURCE
    assert item.requirement_status != RequirementStatus.MATCHES


def test_job_live_source_url_is_preserved():
    assert _job().source_url == "https://example.com/jobs/1"


def test_people_live_source_url_is_preserved():
    item = PeopleCandidate(
        candidate_id="person_1",
        person_type="professor",
        name="Ada",
        public_source_url="https://openalex.org/A1",
    )
    assert item.public_source_url == "https://openalex.org/A1"


def test_embedding_work_is_bounded_by_shortlist():
    items = [_job(candidate_id=f"job_{index}") for index in range(100)]
    assert len(lexical_job_shortlist(items, JobSearchRequest(target_roles=["AI"]))) == 30
    assert len(lexical_job_shortlist(items, JobSearchRequest(target_roles=["AI"]))[:20]) == 20


def test_embedding_cache_reuses_unchanged_content():
    cache = {}

    class Provider:
        model_id = "test"
        dimensions = 2
        calls = 0

        def embed(self, text):
            self.calls += 1
            return [1.0, 0.0]

    class Repository:
        def deactivate_source(self, *_args, **_kwargs):
            return None

        def get_cached_embedding(self, _user, digest, _model, _dimensions):
            return cache.get(digest)

        def upsert_document(self, **values):
            digest = hashlib.sha256(values["text_content"].encode()).hexdigest()
            if values.get("embedding") is not None:
                cache[digest] = values["embedding"]
            return {"retrieval_document_id": values["source_entity_id"]}

    provider = Provider()
    service = HybridRetrievalService(Repository(), embedding_provider=provider)
    item = {
        "corpus_type": "job",
        "user_id": "user",
        "source_entity_id": "job",
        "source_version": "1",
        "title": "Job",
        "text": "unchanged content",
    }
    first = service.index_text_batch([item])
    second = service.index_text_batch([item])
    assert first[1]["embedding_count"] == 1
    assert second[1]["embedding_cache_hit_count"] == 1
    assert provider.calls == 1


def test_provider_timeout_preserves_partial_results():
    def slow():
        time.sleep(0.05)
        return SourceResult(True, "slow", [{"id": 2}])

    results = run_provider_fetches(
        [
            ("fast", "fast", lambda: SourceResult(True, "fast", [{"id": 1}])),
            ("slow", "slow", slow),
        ],
        timeout_seconds=0.01,
    )
    assert results["fast"][0].records == [{"id": 1}]
    assert results["slow"][2] is True


def test_provider_timeout_respects_judge_search_budget():
    started = time.perf_counter()
    results = run_provider_fetches(
        [("slow", "slow", lambda: (time.sleep(0.1), SourceResult(True, "slow"))[1])],
        timeout_seconds=0.01,
    )
    assert results["slow"][2] is True
    assert time.perf_counter() - started < 0.08


def test_structured_job_request_can_skip_expensive_planner():
    parsed = parse_structured_job_request(
        "Find 5 AI engineering internships in California.", {"skills": ["Python"]}
    )
    assert parsed is not None
    assert parsed.requested_count == 5
    assert parsed.locations == ["California"]


@pytest.mark.parametrize(
    "phrase",
    ("intern", "internship", "co-op", "co op"),
)
def test_structured_job_request_recognizes_exact_internship_terms(phrase):
    parsed = parse_structured_job_request(
        f"Find 5 machine learning {phrase} jobs in California.", {}
    )

    assert parsed is not None
    assert parsed.employment_types == ["Internship"]
    assert parsed.target_roles == ["machine learning"]


def test_international_is_not_parsed_as_internship():
    assert (
        parse_structured_job_request(
            "Find 5 international marketing jobs in New York.", {}
        )
        is None
    )


def test_ambiguous_job_request_still_uses_planning_when_needed():
    assert parse_structured_job_request("Help me find a suitable role.", {}) is None


def test_claude_four_uses_us_inference_profile_when_raw_model_id_is_configured():
    raw = "anthropic.claude-sonnet-4-20250514-v1:0"
    active = "us.anthropic.claude-sonnet-4-6"
    assert resolve_bedrock_model_id(raw, "us-east-1") == active
    assert resolve_bedrock_model_id(f"us.{raw}", "us-east-1") == active


def test_job_intent_routes_to_controlled_action_planner():
    from app.graph.career_agent_graph import CareerAgentGraph

    assert CareerAgentGraph._route_after_prepare({"intent": "job_search"}) == "action"


def test_demo_fallback_is_judge_only():
    assert not should_use_demo_fallback(
        judge_mode=False, useful_live_count=0, elapsed_seconds=20
    )
    assert should_use_demo_fallback(
        judge_mode=True, useful_live_count=0, elapsed_seconds=10
    )


def test_demo_fallback_is_explicitly_labeled():
    records = load_demo_search_fixtures("jobs")
    assert records
    assert all(item["is_demo_sample"] for item in records)
    assert all(item["source_status"] == "demo_snapshot" for item in records)


def test_demo_snapshot_result_is_not_claimed_current():
    records = load_demo_search_fixtures("jobs") + load_demo_search_fixtures("people")
    assert records
    assert all(item.get("snapshot_date") for item in records)
    assert all(item.get("source_verified_at_snapshot") is True for item in records)
    assert all(item.get("source_status") == "demo_snapshot" for item in records)
