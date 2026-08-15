"""Independent-process helper for the Cockroach Profile checkpoint gate."""

from __future__ import annotations

import json
import os
import sys
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import func, select


PROFILE = {
    "name": "Synthetic Checkpoint Student",
    "school": "Synthetic University",
    "major": "Computer Science",
    "graduation_year": 2028,
    "skills": ["Python"],
    "experience": [{"organization": "Synthetic Lab", "role": "Intern"}],
}


def _interrupt_value(result):
    values = result.get("__interrupt__") or ()
    return values[0].value if values else None


def _build_graph(*, document_id: str, process_a: bool):
    import app.graph.profile_graph as graph_module
    from app.graph.checkpoint import get_default_checkpointer

    if process_a:
        graph_module.store_document = lambda _state: {
            "document_id": document_id,
            "document_ids": [document_id],
            "stored_documents": [],
            "s3_key": f"checkpoint-test/{document_id}.pdf",
        }
        graph_module.extract_resume = lambda _state: {
            "resume_text": "Synthetic resume text",
            "document_texts": [],
        }
        graph_module.extract_profile = lambda _state: {"extracted_profile": PROFILE}
    else:
        def must_not_restart(_state):
            raise AssertionError("An upstream extraction node restarted after the interrupt.")

        graph_module.store_document = must_not_restart
        graph_module.extract_resume = must_not_restart
        graph_module.extract_profile = must_not_restart
    return graph_module.build_profile_graph(get_default_checkpointer())


def process_a():
    from app.database import init_db, profile_repository

    init_db()
    user = profile_repository.create_demo_user()
    document_id = str(uuid4())
    profile_repository.create_document(
        document_id=document_id,
        user_id=user["user_id"],
        filename="synthetic-checkpoint.pdf",
        s3_key=f"checkpoint-test/{document_id}.pdf",
        document_type="resume",
        content_type="application/pdf",
        size_bytes=128,
    )
    thread_id = f"profile-process-{uuid4()}"
    graph = _build_graph(document_id=document_id, process_a=True)
    result = graph.invoke(
        {"resume_path": "synthetic-checkpoint.pdf", "user_id": user["user_id"]},
        config={"configurable": {"thread_id": thread_id}},
    )
    pending = _interrupt_value(result)
    assert pending and pending["type"] == "confirm_profile"
    print(json.dumps({"thread_id": thread_id, "user_id": user["user_id"], "document_id": document_id}))


def process_b():
    from app.database import profile_repository
    from app.database.database import session_scope
    from app.database.models import (
        ProfileDocumentSource,
        ProfileFieldRevision,
        ProfileVersion,
    )
    from app.graph.checkpoint import _close_default_checkpointer

    thread_id = os.environ["CT_CHECKPOINT_THREAD_ID"]
    user_id = os.environ["CT_CHECKPOINT_USER_ID"]
    document_id = os.environ["CT_CHECKPOINT_DOCUMENT_ID"]
    graph = _build_graph(document_id=document_id, process_a=False)
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    assert snapshot.next == ("confirm_profile",)
    result = graph.invoke(
        Command(resume={"confirmed": True, "profile": PROFILE}), config=config
    )
    assert result["confirmed"] is True
    assert graph.get_state(config).next == ()

    def counts():
        with session_scope(profile_repository.session_factory) as session:
            versions = session.scalar(
                select(func.count(ProfileVersion.version_id)).where(ProfileVersion.user_id == user_id)
            )
            revisions = session.scalars(
                select(ProfileFieldRevision).where(ProfileFieldRevision.user_id == user_id)
            ).all()
            sources = session.scalar(
                select(func.count(ProfileDocumentSource.document_id)).where(
                    ProfileDocumentSource.document_id == document_id
                )
            )
            return int(versions or 0), revisions, int(sources or 0)

    before_versions, before_revisions, before_sources = counts()
    assert before_versions == 1
    assert before_sources == 1
    assert before_revisions
    assert len({item.field_key for item in before_revisions}) == len(before_revisions)
    assert len({item.resulting_profile_version_id for item in before_revisions}) == 1

    # Reading an ended graph after restart must not replay its save side effect.
    final_state = graph.invoke(None, config=config)
    assert final_state["saved_profile"]["profile_version"] == 1
    after_versions, after_revisions, after_sources = counts()
    assert (after_versions, len(after_revisions), after_sources) == (
        before_versions, len(before_revisions), before_sources
    )
    graph.checkpointer.delete_thread(thread_id)
    _close_default_checkpointer()
    print(json.dumps({
        "resumed": True,
        "profile_versions": after_versions,
        "field_revisions": len(after_revisions),
        "document_sources": after_sources,
        "upstream_restarted": False,
    }))


if __name__ == "__main__":
    if sys.argv[1] == "a":
        process_a()
    elif sys.argv[1] == "b":
        process_b()
    else:
        raise SystemExit("Expected process mode a or b.")
