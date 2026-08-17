"""Seed and verify durable state in independent Python processes."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

from sqlalchemy import func, select

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from app.database.models import AgentRun, SearchSession
from app.database.repository import ProfileRepository
from app.services.agent_results import primary_job_link, resolve_agent_display_result


def _repository() -> tuple[ProfileRepository, object]:
    engine = create_database_engine(os.environ["DATABASE_URL"])
    init_db(engine)
    return ProfileRepository(create_session_factory(engine)), engine


def _counts(repository: ProfileRepository) -> tuple[int, int]:
    with session_scope(repository.session_factory) as session:
        return (
            int(session.scalar(select(func.count(AgentRun.run_id))) or 0),
            int(session.scalar(select(func.count(SearchSession.search_session_id))) or 0),
        )


def seed() -> None:
    repository, engine = _repository()
    user = repository.get_or_create_user("Synthetic Student", "phase9@example.test")
    other = repository.get_or_create_user("Other Student", "phase9-other@example.test")
    profile = repository.upsert_profile(
        user["user_id"],
        {
            "school": "Synthetic University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "skills": ["Python", "SQL"],
            "experience": [{"role": "Software Intern", "organization": "Synthetic Lab"}],
        },
    )
    document = repository.create_document(
        document_id="phase9-document",
        user_id=user["user_id"],
        filename="synthetic-resume.pdf",
        s3_key=f"users/{user['user_id']}/documents/phase9-document/synthetic-resume.pdf",
        document_type="resume",
        content_type="application/pdf",
        size_bytes=128,
    )
    conversation = repository.create_conversation(user["user_id"], "Synthetic jobs")
    empty_conversation = repository.create_conversation(user["user_id"], "No search")
    message = repository.add_message(
        user["user_id"], conversation["conversation_id"], "user", "Find ML internships."
    )
    repository.add_message(
        user["user_id"], conversation["conversation_id"], "assistant", "Synthetic results saved."
    )
    candidate = repository.create_memory_candidate(
        user["user_id"],
        category="preference",
        content="remote-friendly internships",
        confidence=1.0,
        source="phase9-synthetic",
        source_conversation_id=conversation["conversation_id"],
        source_message_ids=[message["message_id"]],
    )
    memory = repository.review_memory_candidate(
        user["user_id"], candidate["candidate_id"], accept=True
    )
    run = repository.create_agent_run(
        user["user_id"],
        conversation["conversation_id"],
        goal="Find ML internships",
        user_message_id=message["message_id"],
    )
    search = repository.get_or_create_search_session(
        user["user_id"],
        run["run_id"],
        intent="job_search",
        normalized_request={"target_roles": ["ML Engineer Intern"]},
        requested_count=1,
        source_call_budget=3,
    )
    jobs = [
        {
            "candidate_id": "phase9-job",
            "title": "ML Engineer Intern",
            "company": "Synthetic Company",
            "location": "Los Angeles",
            "application_url": "https://jobs.example.test/apply/phase9-job",
            "source_url": "https://api.example.test/jobs/phase9-job",
            "source_status": "verified_public_source",
            "requirement_status": "requirements_not_fully_verified",
            "hard_constraints_met": False,
        }
    ]
    repository.update_search_session(
        user["user_id"], search["search_session_id"], candidate_records=jobs, status="completed"
    )
    references = {
        "profile": [
            {
                "profile_version_id": profile["profile_version_id"],
                "field": "skills",
                "value": ["Python", "SQL"],
            }
        ],
        "approved_memories": [
            {"category": memory["category"], "content": memory["content"]}
        ],
    }
    repository.update_agent_run(
        user["user_id"],
        run["run_id"],
        intent="job_search",
        status="completed",
        final_summary="Synthetic persisted result.",
        state_json={
            "workflow_stage": "completed",
            "candidate_count": 1,
            "verified_candidate_count": 0,
            "unverified_candidate_count": 1,
            "source_call_count": 3,
            "personalization_references": references,
        },
    )
    print(
        json.dumps(
            {
                "user_id": user["user_id"],
                "other_user_id": other["user_id"],
                "conversation_id": conversation["conversation_id"],
                "empty_conversation_id": empty_conversation["conversation_id"],
                "document_id": document["document_id"],
                "run_id": run["run_id"],
                "search_session_id": search["search_session_id"],
            }
        )
    )
    engine.dispose()


def verify() -> None:
    state = json.loads(os.environ["CT_PHASE9_STATE"])
    repository, engine = _repository()
    before = _counts(repository)
    with (
        patch(
            "app.services.job_search.JobSearchService.search",
            side_effect=AssertionError("provider call during rehydration"),
        ),
        patch("app.llm.model.get_llm", side_effect=AssertionError("model call during rehydration")),
    ):
        restored = resolve_agent_display_result(
            repository, state["user_id"], state["conversation_id"], None
        )
    assert restored["run_id"] == state["run_id"]
    assert restored["job_candidates"][0]["candidate_id"] == "phase9-job"
    assert primary_job_link(restored["job_candidates"][0]) == (
        "View official posting",
        "https://jobs.example.test/apply/phase9-job",
    )
    assert restored["job_candidates"][0]["source_url"] == (
        "https://api.example.test/jobs/phase9-job"
    )
    assert restored["source_call_count"] == 3
    assert restored["personalization_references"]["profile"][0]["value"] == [
        "Python",
        "SQL",
    ]
    assert repository.get_profile(state["user_id"])["school"] == "Synthetic University"
    assert repository.list_memories(state["user_id"])[0]["content"] == (
        "remote-friendly internships"
    )
    assert repository.get_document(state["user_id"], state["document_id"])["filename"] == (
        "synthetic-resume.pdf"
    )
    assert len(repository.get_conversation(state["user_id"], state["conversation_id"])["messages"]) == 2
    assert resolve_agent_display_result(
        repository, state["user_id"], state["empty_conversation_id"], None
    ) == {}
    try:
        repository.get_conversation(state["other_user_id"], state["conversation_id"])
    except ValueError:
        pass
    else:
        raise AssertionError("cross-user conversation access succeeded")
    assert repository.list_documents(state["other_user_id"]) == []
    assert repository.list_memories(state["other_user_id"]) == []
    assert _counts(repository) == before == (1, 1)
    print(json.dumps({"restart": "PASS", "agent_runs": before[0], "search_sessions": before[1]}))
    engine.dispose()


if __name__ == "__main__":
    {"seed": seed, "verify": verify}[sys.argv[1]]()
