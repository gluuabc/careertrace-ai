from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.graph.career_agent_graph import build_career_agent_graph
from app.llm.model import get_llm
from app.services.context_manager import ContextManager
from app.services.skill_registry import skill_registry

ObservableEventHandler = Callable[[dict[str, Any]], None]


def respond_to_user(
    user_id: str,
    conversation_id: str,
    prompt: str,
    repository: ProfileRepository = profile_repository,
    event_handler: ObservableEventHandler | None = None,
) -> str:
    """Persist a request, invoke the bounded Career Agent, and persist its reply."""

    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("A career request is required.")
    user_message = repository.add_message(
        user_id, conversation_id, "user", clean_prompt
    )
    if event_handler:
        event_handler({"type": "status", "workflow_stage": "initializing"})

    context = ContextManager(repository, skill_registry)
    agent = build_career_agent_graph(
        repository=repository,
        context=context,
        registry=skill_registry,
        checkpointer=None,
        model_factory=get_llm,
    )
    result = agent.invoke(
        {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "current_request": clean_prompt,
            "messages": [],
            "user_message_id": user_message["message_id"],
        }
    )
    response = str(result.get("final_response") or "").strip()
    if not response:
        raise ValueError("The Career Agent returned an empty response.")
    assistant_message = repository.add_message(
        user_id,
        conversation_id,
        "assistant",
        response,
        reply_to_message_id=user_message["message_id"],
    )
    if result.get("run_id"):
        repository.update_agent_run(
            user_id,
            result["run_id"],
            assistant_message_id=assistant_message["message_id"],
        )
    if event_handler:
        event_handler(
            {
                "type": "final",
                "run_id": result.get("run_id"),
                "conversation_id": conversation_id,
                "status": result.get("status"),
                "todo_items": result.get("todo_items", []),
                "warnings": result.get("warnings", []),
                "job_candidates": result.get("job_candidates", []),
                "people_candidates": result.get("people_candidates", []),
                "workflow_stage": result.get("workflow_stage"),
            }
        )
    return response
