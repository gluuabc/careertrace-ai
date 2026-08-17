from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.auth.judge import (
    JudgeAccessError,
    hash_recovery_code,
    resume_judge_workspace,
    start_judge_workspace,
)
from app.auth.session import clear_auth_state, set_active_identity
from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from app.database.models import JudgeWorkspaceCredential
from app.database.repository import ProfileRepository


ACCESS = "hackathon-shared-access"


@pytest.fixture
def workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    with patch.dict(
        os.environ,
        {"JUDGE_DEMO_ENABLED": "true", "JUDGE_DEMO_ACCESS_CODE": ACCESS},
        clear=False,
    ):
        yield repository
    engine.dispose()


def _start(repository):
    return start_judge_workspace(ACCESS, repository)


def _profile():
    return {
        "school": "Synthetic University",
        "major": "Computer Science",
        "graduation_year": 2028,
        "skills": ["Python"],
        "experience": [{"organization": "Demo Lab", "role": "Intern"}],
    }


def _approve_memory(repository, user_id):
    candidate = repository.create_memory_candidate(
        user_id, category="preference", content="Prefer remote roles",
        confidence=1, source="judge conversation",
    )
    with patch("app.services.retrieval_corpus.RetrievalCorpusIndexer.index_memory", return_value=[]):
        return repository.review_memory_candidate(user_id, candidate["candidate_id"], accept=True)


def test_judge_entry_requires_correct_access_code(workspace):
    with pytest.raises(JudgeAccessError, match="invalid"):
        start_judge_workspace("wrong", workspace)
    with patch.dict(os.environ, {"JUDGE_DEMO_ENABLED": "false"}, clear=False):
        with pytest.raises(JudgeAccessError):
            start_judge_workspace(ACCESS, workspace)


def test_judge_workspace_generates_unique_user(workspace):
    first, first_code = _start(workspace)
    second, second_code = _start(workspace)
    assert first["user_id"] != second["user_id"]
    assert first_code != second_code
    assert first_code.startswith("CT-") and len(first_code) == 22


def test_recovery_code_plaintext_is_not_stored(workspace):
    user, recovery_code = _start(workspace)
    with session_scope(workspace.session_factory) as session:
        credential = session.scalar(
            select(JudgeWorkspaceCredential).where(
                JudgeWorkspaceCredential.user_id == user["user_id"]
            )
        )
        assert credential.recovery_code_hash == hash_recovery_code(recovery_code)
        assert recovery_code not in credential.recovery_code_hash


def test_invalid_recovery_code_cannot_load_workspace(workspace):
    _start(workspace)
    with pytest.raises(JudgeAccessError, match="recovery failed"):
        resume_judge_workspace(ACCESS, "CT-2222-2222-2222-2222", workspace)


def test_two_judge_workspaces_are_isolated(workspace):
    first, first_code = _start(workspace)
    second, second_code = _start(workspace)
    workspace.upsert_profile(first["user_id"], _profile())
    assert workspace.get_profile(second["user_id"]) is None
    assert resume_judge_workspace(ACCESS, first_code, workspace)["user_id"] == first["user_id"]
    assert resume_judge_workspace(ACCESS, second_code, workspace)["user_id"] == second["user_id"]


def test_google_user_cannot_assume_judge_workspace(workspace):
    google = workspace.get_or_create_google_user(
        google_id="google-1", email="ada@example.com", name="Ada"
    )
    _, recovery_code = _start(workspace)
    recovered = resume_judge_workspace(ACCESS, recovery_code, workspace)
    assert recovered["user_id"] != google["user_id"]
    with pytest.raises(ValueError, match="demo"):
        set_active_identity({}, google, "judge")


def test_logout_does_not_delete_judge_workspace(workspace):
    user, recovery_code = _start(workspace)
    state = {}
    set_active_identity(state, user, "judge")
    clear_auth_state(state)
    assert resume_judge_workspace(ACCESS, recovery_code, workspace)["user_id"] == user["user_id"]


def test_logout_login_restores_same_judge_user(workspace):
    user, recovery_code = _start(workspace)
    state = {}
    set_active_identity(state, user, "judge")
    clear_auth_state(state)
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    set_active_identity(state, restored, "judge")
    assert state["current_user_id"] == user["user_id"]


def test_logout_login_restores_profile(workspace):
    user, recovery_code = _start(workspace)
    workspace.upsert_profile(user["user_id"], _profile())
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    assert workspace.get_profile(restored["user_id"])["major"] == "Computer Science"


def test_logout_login_restores_approved_memories(workspace):
    user, recovery_code = _start(workspace)
    memory = _approve_memory(workspace, user["user_id"])
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    assert {item["memory_id"] for item in workspace.list_memories(restored["user_id"])} == {memory["memory_id"]}


def test_logout_login_restores_pending_candidates(workspace):
    user, recovery_code = _start(workspace)
    candidate = workspace.create_memory_candidate(
        user["user_id"], category="goal", content="Become an engineer",
        confidence=0.9, source="conversation",
    )
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    assert {item["candidate_id"] for item in workspace.list_memory_candidates(restored["user_id"])} == {candidate["candidate_id"]}


def test_logout_login_restores_conversation_history(workspace):
    user, recovery_code = _start(workspace)
    conversation = workspace.create_conversation(user["user_id"], "Judge history")
    workspace.add_message(user["user_id"], conversation["conversation_id"], "user", "First question")
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    messages = workspace.get_conversation(restored["user_id"], conversation["conversation_id"])["messages"]
    assert [item["content"] for item in messages] == ["First question"]


def test_reopened_conversation_continues_prior_messages(workspace):
    user, recovery_code = _start(workspace)
    conversation = workspace.create_conversation(user["user_id"], "Continue me")
    first = workspace.add_message(user["user_id"], conversation["conversation_id"], "user", "First")
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    workspace.add_message(restored["user_id"], conversation["conversation_id"], "assistant", "Second", reply_to_message_id=first["message_id"])
    messages = workspace.get_conversation(restored["user_id"], conversation["conversation_id"])["messages"]
    assert [item["content"] for item in messages] == ["First", "Second"]


def test_conversation_rename_persists_after_recovery(workspace):
    user, recovery_code = _start(workspace)
    conversation = workspace.create_conversation(user["user_id"], "Before")
    workspace.rename_conversation(user["user_id"], conversation["conversation_id"], "After")
    restored = resume_judge_workspace(ACCESS, recovery_code, workspace)
    assert workspace.get_conversation(restored["user_id"], conversation["conversation_id"])["title"] == "After"
