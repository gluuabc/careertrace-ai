from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.auth.session import clear_auth_state
from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from app.database.models import AgentRun, SearchSession
from app.database.repository import ProfileRepository
from app.services.agent_results import primary_job_link, resolve_agent_display_result


@pytest.fixture
def persisted_search():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Recovered Judge", "recovered@example.com")
    other = repository.get_or_create_user("Other Judge", "other-recovered@example.com")
    profile = repository.upsert_profile(
        user["user_id"],
        {
            "school": "Synthetic University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "skills": ["Python"],
            "experience": [{"role": "Intern"}],
        },
    )
    conversation = repository.create_conversation(user["user_id"], "Jobs")
    other_conversation = repository.create_conversation(user["user_id"], "Guidance")
    run = repository.create_agent_run(
        user["user_id"], conversation["conversation_id"], goal="Find jobs"
    )
    search = repository.get_or_create_search_session(
        user["user_id"],
        run["run_id"],
        intent="job_search",
        normalized_request={"target_roles": ["ML Engineer"]},
        requested_count=2,
        source_call_budget=5,
    )
    candidates = [
        {
            "candidate_id": "job-one",
            "title": "ML Engineer Intern",
            "company": "Example",
            "source_name": "greenhouse",
            "source_url": "https://boards-api.greenhouse.io/v1/boards/example/jobs/1",
            "application_url": "https://boards.greenhouse.io/example/jobs/1",
            "hard_constraints_met": True,
        },
        {
            "candidate_id": "job-two",
            "title": "Software Engineer Intern",
            "company": "Example",
            "source_name": "greenhouse",
            "source_url": "https://boards-api.greenhouse.io/v1/boards/example/jobs/2",
            "application_url": "https://boards.greenhouse.io/example/jobs/2",
            "hard_constraints_met": False,
        },
    ]
    repository.update_search_session(
        user["user_id"], search["search_session_id"], candidate_records=candidates
    )
    references = {
        "profile": [
            {
                "profile_version_id": profile["profile_version_id"],
                "field": "skills",
                "value": ["Python"],
            }
        ],
        "approved_memories": [],
    }
    repository.update_agent_run(
        user["user_id"],
        run["run_id"],
        intent="job_search",
        status="completed",
        state_json={
            "workflow_stage": "completed",
            "todo_items": [{"content": "Search", "status": "completed"}],
            "status": {"workflow_stage": "completed", "source_call_count": 5},
            "warnings": ["Synthetic partial source warning"],
            "candidate_count": 2,
            "verified_candidate_count": 1,
            "unverified_candidate_count": 1,
            "source_call_count": 5,
            "personalization_references": references,
        },
    )
    yield {
        "repository": repository,
        "user": user,
        "other": other,
        "conversation": conversation,
        "other_conversation": other_conversation,
        "run": run,
        "search": search,
        "candidates": candidates,
        "references": references,
    }
    engine.dispose()


def _row_counts(repository):
    with session_scope(repository.session_factory) as session:
        return (
            session.scalar(select(func.count(AgentRun.run_id))),
            session.scalar(select(func.count(SearchSession.search_session_id))),
        )


def test_logout_recovery_rehydrates_same_structured_result_without_side_effects(
    persisted_search,
):
    item = persisted_search
    repository = item["repository"]
    state = {
        "current_user_id": item["user"]["user_id"],
        "active_conversation_id": item["conversation"]["conversation_id"],
        "agent_last_result": {"transient": True},
    }
    clear_auth_state(state)
    before = _row_counts(repository)

    with (
        patch(
            "app.services.job_search.JobSearchService.search",
            side_effect=AssertionError("provider called"),
        ),
        patch(
            "app.services.people_search.PeopleSearchService.search",
            side_effect=AssertionError("provider called"),
        ),
        patch("app.llm.model.get_llm", side_effect=AssertionError("model called")),
    ):
        result = resolve_agent_display_result(
            repository,
            item["user"]["user_id"],
            item["conversation"]["conversation_id"],
            state.get("agent_last_result"),
        )

    assert [candidate["candidate_id"] for candidate in result["job_candidates"]] == [
        "job-one",
        "job-two",
    ]
    assert [candidate["application_url"] for candidate in result["job_candidates"]] == [
        candidate["application_url"] for candidate in item["candidates"]
    ]
    assert result["candidate_count"] == 2
    assert result["verified_candidate_count"] == 1
    assert result["unverified_candidate_count"] == 1
    assert result["source_call_count"] == 5
    assert result["personalization_references"] == item["references"]
    assert _row_counts(repository) == before


def test_new_process_without_cached_result_rehydrates_from_sql(persisted_search):
    item = persisted_search

    result = resolve_agent_display_result(
        item["repository"],
        item["user"]["user_id"],
        item["conversation"]["conversation_id"],
        None,
    )

    assert result["run_id"] == item["run"]["run_id"]
    assert result["job_candidates"][0]["candidate_id"] == "job-one"


def test_different_conversation_does_not_inherit_search_results(persisted_search):
    item = persisted_search
    run = item["repository"].create_agent_run(
        item["user"]["user_id"],
        item["other_conversation"]["conversation_id"],
        goal="Compare roles",
    )
    item["repository"].update_agent_run(
        item["user"]["user_id"],
        run["run_id"],
        intent="concise_guidance",
        status="completed",
        state_json={"workflow_stage": "completed"},
    )

    result = resolve_agent_display_result(
        item["repository"],
        item["user"]["user_id"],
        item["other_conversation"]["conversation_id"],
        None,
    )

    assert result["run_id"] == run["run_id"]
    assert result["job_candidates"] == []


def test_people_search_uses_same_conversation_scoped_rehydration(persisted_search):
    item = persisted_search
    conversation = item["repository"].create_conversation(
        item["user"]["user_id"], "People"
    )
    run = item["repository"].create_agent_run(
        item["user"]["user_id"], conversation["conversation_id"], goal="Find alumni"
    )
    search = item["repository"].get_or_create_search_session(
        item["user"]["user_id"],
        run["run_id"],
        intent="people_search",
        normalized_request={"person_type": "alumni"},
        requested_count=1,
        source_call_budget=2,
    )
    item["repository"].update_search_session(
        item["user"]["user_id"],
        search["search_session_id"],
        candidate_records=[
            {
                "candidate_id": "person-one",
                "person_type": "alumni",
                "name": "Synthetic Alum",
                "public_source_url": "https://example.com/alum",
            }
        ],
    )
    item["repository"].update_agent_run(
        item["user"]["user_id"],
        run["run_id"],
        intent="people_search",
        status="completed",
        state_json={"workflow_stage": "completed", "candidate_count": 1},
    )

    result = resolve_agent_display_result(
        item["repository"],
        item["user"]["user_id"],
        conversation["conversation_id"],
        None,
    )

    assert result["job_candidates"] == []
    assert result["people_candidates"][0]["candidate_id"] == "person-one"


def test_different_user_cannot_load_run_or_references(persisted_search):
    item = persisted_search
    with pytest.raises(ValueError, match="not found"):
        resolve_agent_display_result(
            item["repository"],
            item["other"]["user_id"],
            item["conversation"]["conversation_id"],
            None,
        )


def test_historical_references_are_not_recomputed_from_current_profile(
    persisted_search,
):
    item = persisted_search
    item["repository"].upsert_profile(
        item["user"]["user_id"],
        {
            "school": "Synthetic University",
            "major": "Data Science",
            "graduation_year": 2029,
            "skills": ["Rust"],
            "experience": [{"role": "Researcher"}],
        },
    )

    result = resolve_agent_display_result(
        item["repository"],
        item["user"]["user_id"],
        item["conversation"]["conversation_id"],
        None,
    )

    assert result["personalization_references"] == item["references"]
    assert result["personalization_references"]["profile"][0]["value"] == [
        "Python"
    ]


def test_matching_fresh_result_avoids_rehydration():
    cached = {
        "user_id": "user-one",
        "conversation_id": "conversation-one",
        "job_candidates": [{"candidate_id": "fresh"}],
    }

    class Repository:
        def get_latest_agent_display_result(self, *_args):
            raise AssertionError("SQL reconstruction should not run")

    assert (
        resolve_agent_display_result(
            Repository(), "user-one", "conversation-one", cached
        )
        is cached
    )


def test_greenhouse_primary_link_prefers_human_posting(persisted_search):
    candidate = persisted_search["candidates"][0]

    assert primary_job_link(candidate) == (
        "View official posting",
        candidate["application_url"],
    )
    assert candidate["source_url"].startswith("https://boards-api.greenhouse.io/")
