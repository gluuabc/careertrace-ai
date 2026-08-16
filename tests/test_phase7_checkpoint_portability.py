from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph import checkpoint as checkpoint_module
from app.nodes.confirmation import confirm_profile


LIVE_COCKROACH = bool(os.getenv("COCKROACH_TEST_DATABASE_URL"))
LIVE_ONLY = pytest.mark.skipif(
    not LIVE_COCKROACH, reason="isolated Cockroach test URL not configured"
)
CHECKPOINT_SCHEMA = "careertrace_checkpoint_test"


@pytest.fixture(scope="module")
def primitive_checkpoint_result():
    if not LIVE_COCKROACH:
        pytest.skip("isolated Cockroach test URL not configured")
    database_url = os.environ["COCKROACH_TEST_DATABASE_URL"]
    thread_id = f"pytest-primitive-{uuid4()}"
    base_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"primitive": {"status": "written"}}
    checkpoint["channel_versions"] = {"primitive": "1"}
    saver_a, context_a = checkpoint_module.create_cockroach_checkpointer(
        database_url, CHECKPOINT_SCHEMA
    )
    saved_config = saver_a.put(
        base_config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {"primitive": "1"},
    )
    saver_a.put_writes(
        saved_config,
        [("intermediate", {"status": "written"})],
        task_id="pytest-primitive-task",
    )
    first = saver_a.get_tuple(saved_config)
    listed_first = list(saver_a.list(base_config, limit=10))
    context_a.__exit__(None, None, None)

    saver_b, context_b = checkpoint_module.create_cockroach_checkpointer(
        database_url, CHECKPOINT_SCHEMA
    )
    reopened = saver_b.get_tuple(saved_config)
    listed_reopened = list(saver_b.list(base_config, limit=10))
    result = {
        "first": first,
        "listed_first": listed_first,
        "reopened": reopened,
        "listed_reopened": listed_reopened,
    }
    yield result
    saver_b.delete_thread(thread_id)
    context_b.__exit__(None, None, None)


@pytest.fixture(scope="module")
def graph_checkpoint_result():
    if not LIVE_COCKROACH:
        pytest.skip("isolated Cockroach test URL not configured")
    database_url = os.environ["COCKROACH_TEST_DATABASE_URL"]

    class State(dict):
        pass

    def build(saver):
        from app.state.schema import ProfileState

        builder = StateGraph(ProfileState)
        builder.add_node("confirm_profile", confirm_profile)
        builder.add_edge(START, "confirm_profile")
        builder.add_edge("confirm_profile", END)
        return builder.compile(checkpointer=saver)

    thread_id = f"pytest-graph-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    profile = {
        "school": "Synthetic University",
        "major": "Computer Science",
        "graduation_year": 2028,
        "skills": ["Python"],
        "experience": [{"organization": "Synthetic Lab", "role": "Intern"}],
    }
    saver_a, context_a = checkpoint_module.create_cockroach_checkpointer(
        database_url, CHECKPOINT_SCHEMA
    )
    interrupted = build(saver_a).invoke({"extracted_profile": profile}, config=config)
    context_a.__exit__(None, None, None)

    saver_b, context_b = checkpoint_module.create_cockroach_checkpointer(
        database_url, CHECKPOINT_SCHEMA
    )
    graph_b = build(saver_b)
    pending = graph_b.get_state(config)
    resumed = graph_b.invoke(
        Command(resume={"confirmed": True, "profile": profile}), config=config
    )
    ended = graph_b.get_state(config)
    result = {
        "interrupted": interrupted,
        "pending": pending,
        "resumed": resumed,
        "ended": ended,
    }
    yield result
    saver_b.delete_thread(thread_id)
    context_b.__exit__(None, None, None)


@pytest.fixture(scope="module")
def process_restart_result():
    if not LIVE_COCKROACH:
        pytest.skip("isolated Cockroach test URL not configured")
    env = os.environ.copy()
    env["DATABASE_URL"] = env["COCKROACH_TEST_DATABASE_URL"]
    env["LANGGRAPH_CHECKPOINT_BACKEND"] = "cockroachdb"
    env["LANGGRAPH_CHECKPOINT_SCHEMA"] = CHECKPOINT_SCHEMA
    sentinel = Path(f"/tmp/careertrace-checkpoint-sentinel-{uuid4()}.sqlite")
    env["LANGGRAPH_CHECKPOINT_DB"] = str(sentinel)
    helper = Path(__file__).with_name("checkpoint_process_helper.py")
    repository_root = helper.parent.parent
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{repository_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(repository_root)
    )

    first = subprocess.run(
        [sys.executable, str(helper), "a"],
        env=env,
        cwd=repository_root,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert first.returncode == 0, first.stderr
    identity = json.loads(first.stdout.strip().splitlines()[-1])
    env.update(
        {
            "CT_CHECKPOINT_THREAD_ID": identity["thread_id"],
            "CT_CHECKPOINT_USER_ID": identity["user_id"],
            "CT_CHECKPOINT_DOCUMENT_ID": identity["document_id"],
        }
    )
    second = subprocess.run(
        [sys.executable, str(helper), "b"],
        env=env,
        cwd=repository_root,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert second.returncode == 0, second.stderr
    return {
        **json.loads(second.stdout.strip().splitlines()[-1]),
        "local_checkpoint_exists": sentinel.exists(),
    }


@LIVE_ONLY
def test_cockroach_checkpointer_setup_and_write(primitive_checkpoint_result):
    assert primitive_checkpoint_result["first"] is not None
    assert primitive_checkpoint_result["first"].checkpoint["channel_values"]["primitive"]["status"] == "written"


@LIVE_ONLY
def test_cockroach_checkpointer_read_after_new_connection(primitive_checkpoint_result):
    assert primitive_checkpoint_result["reopened"] is not None
    assert primitive_checkpoint_result["reopened"].checkpoint["channel_values"]["primitive"]["status"] == "written"


@LIVE_ONLY
def test_cockroach_checkpointer_list(primitive_checkpoint_result):
    assert primitive_checkpoint_result["listed_first"]
    assert primitive_checkpoint_result["listed_reopened"]


@LIVE_ONLY
def test_cockroach_checkpointer_compiles_with_langgraph(graph_checkpoint_result):
    assert graph_checkpoint_result["interrupted"].get("__interrupt__")


@LIVE_ONLY
def test_cockroach_checkpointer_interrupt_resume(graph_checkpoint_result):
    assert graph_checkpoint_result["pending"].next == ("confirm_profile",)
    assert graph_checkpoint_result["resumed"]["confirmed"] is True
    assert graph_checkpoint_result["ended"].next == ()


@LIVE_ONLY
def test_cockroach_checkpointer_survives_true_process_restart(process_restart_result):
    assert process_restart_result["resumed"] is True


@LIVE_ONLY
def test_cockroach_profile_workflow_resumes_after_process_restart(process_restart_result):
    assert process_restart_result["upstream_restarted"] is False
    assert process_restart_result["profile_versions"] == 1


@LIVE_ONLY
def test_cockroach_profile_resume_does_not_duplicate_profile_side_effects(process_restart_result):
    assert process_restart_result["profile_versions"] == 1
    assert process_restart_result["field_revisions"] > 0
    assert process_restart_result["document_sources"] == 1


def test_local_sqlite_mode_still_uses_sqlite_checkpointer(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver

    checkpoint_module._close_default_checkpointer()
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_BACKEND", "sqlite")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB", str(tmp_path / "local.sqlite"))
    saver = checkpoint_module.get_default_checkpointer()
    assert isinstance(saver, SqliteSaver)
    checkpoint_module._close_default_checkpointer()


@LIVE_ONLY
def test_deployed_cockroach_mode_uses_cockroach_checkpointer():
    from langchain_cockroachdb import CockroachDBSaver

    saver, context = checkpoint_module.create_cockroach_checkpointer(
        os.environ["COCKROACH_TEST_DATABASE_URL"], CHECKPOINT_SCHEMA
    )
    try:
        assert isinstance(saver, CockroachDBSaver)
    finally:
        context.__exit__(None, None, None)


@LIVE_ONLY
def test_deployed_checkpoint_does_not_depend_on_local_checkpoint_file(process_restart_result):
    assert process_restart_result["local_checkpoint_exists"] is False
