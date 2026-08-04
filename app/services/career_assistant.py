import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.database.repository import ProfileRepository, profile_repository
from app.llm.model import get_llm


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content).strip()


def respond_to_user(
    user_id: str,
    conversation_id: str,
    prompt: str,
    repository: ProfileRepository = profile_repository,
) -> str:
    """Generate and persist chat without modifying career memory or profile."""

    repository.add_message(user_id, conversation_id, "user", prompt)
    conversation = repository.get_conversation(user_id, conversation_id)
    profile = repository.get_profile(user_id)
    memories = repository.list_memories(user_id)
    profile_context = None
    if profile:
        profile_context = {
            key: value
            for key, value in profile.items()
            if key
            not in {
                "user_id",
                "email",
                "updated_at",
                "source_documents",
                "profile_changed",
            }
        }
    context = {
        "confirmed_profile": profile_context,
        "approved_flexible_memories": [
            {"category": item["category"], "content": item["content"]}
            for item in memories
        ],
    }
    messages = [
        SystemMessage(
            content=(
                "You are CareerTrace, a practical career assistant. Use only the "
                "provided confirmed profile and approved memories as personal "
                "context. Do not claim that chat changes the user's stored profile "
                "or memory. Give concise, concrete career guidance.\n\n"
                f"CONTEXT:\n{json.dumps(context, indent=2)}"
            )
        )
    ]
    for item in conversation["messages"][-20:]:
        if item["role"] == "user":
            messages.append(HumanMessage(content=item["content"]))
        else:
            messages.append(AIMessage(content=item["content"]))
    response = get_llm("reasoning").invoke(messages)
    text = _message_text(response.content)
    if not text:
        raise ValueError("The career assistant returned an empty response.")
    repository.add_message(user_id, conversation_id, "assistant", text)
    return text
