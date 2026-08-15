from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.database.repository import ProfileRepository, profile_repository
from app.llm.model import get_llm
from app.services.token_accounting import heuristic_text_tokens


class ExtractedMemoryProposal(BaseModel):
    destination: Literal["profile", "memory"]
    category: Literal["profile_fact", "preference", "goal", "constraint", "event"]
    field_key: str | None = None
    operation: Literal["add", "replace", "remove"]
    values: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    event_time: datetime | None = None
    raw_temporal_expression: str | None = None


class MemoryExtractionOutput(BaseModel):
    proposals: list[ExtractedMemoryProposal] = Field(default_factory=list)


def _messages_after_watermark(
    conversation: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    messages = list(conversation.get("messages") or [])
    start = state.get("last_memory_extraction_message_id")
    boundary = state.get("pending_boundary_message_id")
    start_index = next(
        (index for index, item in enumerate(messages) if item["message_id"] == start),
        -1,
    )
    end_index = next(
        (index for index, item in enumerate(messages) if item["message_id"] == boundary),
        len(messages) - 1,
    )
    return messages[start_index + 1 : end_index + 1]


def build_memory_extraction_input(
    repository: ProfileRepository,
    user_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Build original short segments or signal-selected long context within budget."""

    conversation = repository.get_conversation(user_id, conversation_id)
    state = repository.get_conversation_memory_state(user_id, conversation_id)
    if not state or not state.get("pending_boundary_message_id"):
        raise ValueError("Conversation has no pending memory extraction boundary.")
    segment = _messages_after_watermark(conversation, state)
    segment_ids = {item["message_id"] for item in segment}
    signals = [
        item
        for item in repository.list_conversation_memory_signals(user_id, conversation_id)
        if item["source_message_id"] in segment_ids
    ]
    max_tokens = max(1000, int(os.getenv("MEMORY_EXTRACTION_MAX_INPUT_TOKENS", "6000")))
    original_tokens = heuristic_text_tokens(json.dumps(segment, default=str))
    user_turns = sum(item["role"] == "user" for item in segment)
    marked_ids = {item["source_message_id"] for item in signals}
    if user_turns <= 8 and original_tokens <= 5000:
        selected = segment
        mode = "entire_original_segment"
    else:
        index_by_id = {item["message_id"]: index for index, item in enumerate(segment)}
        required = {index_by_id[item] for item in marked_ids if item in index_by_id}
        candidates: list[tuple[int, int]] = []
        for signal in signals:
            center = index_by_id.get(signal["source_message_id"])
            if center is None:
                continue
            radius = 4 if signal["type"] == "memory.event" else 2
            for index in range(max(0, center - radius), min(len(segment), center + radius + 1)):
                if index not in required:
                    candidates.append((abs(index - center), index))
        selected_indexes = set(required)
        for _, index in sorted(set(candidates)):
            trial = [segment[item] for item in sorted([*selected_indexes, index])]
            if heuristic_text_tokens(json.dumps(trial, default=str)) <= max_tokens:
                selected_indexes.add(index)
        selected = [segment[index] for index in sorted(selected_indexes)]
        mode = "signal_selected_context"
    # Marked statements are non-negotiable. If they alone exceed the budget, retain
    # them and report the actual count instead of deleting the user's explicit fact.
    selected_tokens = heuristic_text_tokens(json.dumps(selected, default=str))
    profile = repository.get_profile(user_id) or {}
    referenced_fields = sorted(
        {item["type"].split(".", 1)[1] for item in signals if item["type"].startswith("profile.")}
    )
    memory_types = {
        item["type"].split(".", 1)[1]
        for item in signals
        if item["type"].startswith("memory.")
    }
    relevant_memories = [
        item for item in repository.list_memories(user_id)
        if item["category"] in memory_types
    ][:5]
    groups: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        groups.setdefault(signal["type"], []).append(signal)
    return {
        "conversation_id": conversation_id,
        "conversation_title": conversation["title"],
        "segment_start_timestamp": segment[0]["created_at"] if segment else None,
        "segment_end_timestamp": segment[-1]["created_at"] if segment else None,
        "extraction_boundary_timestamp": segment[-1]["created_at"] if segment else None,
        "start_watermark_message_id": state.get("last_memory_extraction_message_id"),
        "end_boundary_message_id": state["pending_boundary_message_id"],
        "mode": mode,
        "messages": selected,
        "signals": signals,
        "signals_grouped_chronologically": groups,
        "relevant_profile": {field: profile.get(field) for field in referenced_fields},
        "relevant_approved_memories": relevant_memories,
        "estimated_input_tokens": selected_tokens,
        "max_input_tokens": max_tokens,
        "marked_source_message_ids": sorted(marked_ids),
    }


def _event_time(values: list[str], source_created_at: str | None) -> tuple[datetime | None, str | None]:
    text = " ".join(values)
    raw = next((item for item in ("yesterday", "today") if re.search(rf"\b{item}\b", text, re.I)), None)
    if not raw or not source_created_at:
        return None, raw
    created = datetime.fromisoformat(source_created_at)
    return (created - timedelta(days=1) if raw == "yesterday" else created), raw


def _deterministic_proposals(payload: dict[str, Any]) -> list[ExtractedMemoryProposal]:
    by_message = {item["message_id"]: item for item in payload["messages"]}
    proposals = []
    for signal_type, signals in payload["signals_grouped_chronologically"].items():
        latest = signals[-1]
        message_ids = [item["source_message_id"] for item in signals]
        if signal_type.startswith("profile."):
            proposals.append(ExtractedMemoryProposal(
                destination="profile", category="profile_fact",
                field_key=signal_type.split(".", 1)[1],
                operation=latest["operation_hint"], values=latest["value_hint"],
                source_message_ids=message_ids,
            ))
        else:
            category = signal_type.split(".", 1)[1]
            event_time = None
            raw_temporal = None
            if category == "event":
                source = by_message.get(latest["source_message_id"], {})
                event_time, raw_temporal = _event_time(latest["value_hint"], source.get("created_at"))
            proposals.append(ExtractedMemoryProposal(
                destination="memory", category=category,
                operation=latest["operation_hint"], values=latest["value_hint"],
                source_message_ids=message_ids, event_time=event_time,
                raw_temporal_expression=raw_temporal,
            ))
    return proposals


class ConversationMemoryExtractor:
    def __init__(self, repository: ProfileRepository = profile_repository, model_factory=get_llm):
        self.repository = repository
        self.model_factory = model_factory

    def extract(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        state = self.repository.get_conversation_memory_state(user_id, conversation_id)
        if not state or not state.get("pending"):
            return None
        payload = build_memory_extraction_input(self.repository, user_id, conversation_id)
        run = self.repository.create_memory_extraction_run(
            user_id, conversation_id,
            start_watermark_message_id=payload["start_watermark_message_id"],
            end_boundary_message_id=payload["end_boundary_message_id"],
            input_mode=payload["mode"], input_token_count=payload["estimated_input_tokens"],
        )
        if run["status"] == "completed":
            return run
        try:
            prompt = [HumanMessage(content=(
                "Extract only explicit durable candidate proposals from this bounded "
                "conversation segment. Profile fields win over flexible memory. Compare "
                "same-type statements chronologically. Do not invent values or event times.\n"
                + json.dumps(payload, ensure_ascii=False, default=str)
            ))]
            try:
                output = self.model_factory("cheap").with_structured_output(
                    MemoryExtractionOutput
                ).invoke(prompt)
                parsed = output if isinstance(output, MemoryExtractionOutput) else MemoryExtractionOutput.model_validate(output)
                proposals = parsed.proposals
            except Exception:
                proposals = _deterministic_proposals(payload)
            self._persist_proposals(user_id, conversation_id, run["extraction_run_id"], payload, proposals)
            return self.repository.finish_memory_extraction_run(
                user_id, run["extraction_run_id"], success=True
            )
        except Exception as error:
            self.repository.finish_memory_extraction_run(
                user_id, run["extraction_run_id"], success=False,
                error_summary=type(error).__name__,
            )
            raise

    def _persist_proposals(self, user_id: str, conversation_id: str, run_id: str, payload: dict[str, Any], proposals: list[ExtractedMemoryProposal]) -> None:
        profile = self.repository.get_profile(user_id) or {}
        profile_changes = []
        for proposal in proposals:
            if proposal.destination == "profile" and proposal.field_key:
                before = profile.get(proposal.field_key)
                proposed: Any = proposal.values
                if proposal.field_key not in {"skills", "projects", "experience", "education"}:
                    proposed = proposal.values[-1] if proposal.values else None
                    if proposal.field_key == "graduation_year" and str(proposed).isdigit():
                        proposed = int(proposed)
                elif proposal.operation == "add":
                    current = list(before or [])
                    existing = {str(item).casefold() for item in current}
                    proposed = [*current, *(value for value in proposal.values if value.casefold() not in existing)]
                elif proposal.operation == "remove":
                    removed = {value.casefold() for value in proposal.values}
                    proposed = [item for item in list(before or []) if str(item).casefold() not in removed]
                profile_changes.append({
                    "field_key": proposal.field_key, "operation": proposal.operation,
                    "before_value": before, "proposed_value": proposed,
                    "source": {"extraction_run_id": run_id, "message_ids": proposal.source_message_ids},
                })
            elif proposal.destination == "memory":
                self._persist_memory_candidate(user_id, conversation_id, run_id, proposal)
        if profile_changes:
            self.repository.create_profile_revision_draft(
                user_id, source_type="conversation",
                source_conversation_id=conversation_id,
                source_message_ids=sorted({item for change in profile_changes for item in change["source"]["message_ids"]}),
                changes=profile_changes,
            )

    def _persist_memory_candidate(self, user_id: str, conversation_id: str, run_id: str, proposal: ExtractedMemoryProposal) -> None:
        content = "; ".join(proposal.values).strip()
        if not content:
            return
        active = [item for item in self.repository.list_memories(user_id) if item["category"] == proposal.category]
        normalized = " ".join(content.casefold().split())
        exact = next((item for item in active if " ".join(item["content"].casefold().split()) == normalized), None)
        if exact and proposal.operation != "remove":
            return  # NOOP remains visible in the extraction audit, not review UI.
        existing = exact or (active[0] if active else None)
        source_messages = self.repository.get_conversation(user_id, conversation_id)["messages"]
        source_text = " ".join(item["content"] for item in source_messages if item["message_id"] in proposal.source_message_ids).casefold()
        if proposal.operation == "remove":
            operation = "REVOKE"
        elif existing and any(term in source_text for term in ("actually", "instead", "no longer", "now prefer")):
            operation = "UPDATE"
        elif existing:
            operation = "CONFLICT"
        else:
            operation = "ADD"
        self.repository.create_memory_candidate(
            user_id, category=proposal.category, content=content, confidence=1.0,
            source="conversation_extraction", operation=operation,
            existing_memory_id=existing["memory_id"] if existing else None,
            source_conversation_id=conversation_id,
            source_message_ids=proposal.source_message_ids,
            extraction_run_id=run_id, event_time=proposal.event_time,
            raw_temporal_expression=proposal.raw_temporal_expression,
        )


def recover_pending_conversation_extractions(
    user_id: str,
    repository: ProfileRepository = profile_repository,
    model_factory=get_llm,
) -> list[dict[str, Any]]:
    extractor = ConversationMemoryExtractor(repository, model_factory)
    results = []
    for state in repository.list_pending_conversation_memory_states(user_id):
        if state.get("pending_boundary_message_id"):
            result = extractor.extract(user_id, state["conversation_id"])
            if result:
                results.append(result)
    return results


def trigger_conversation_boundary(
    user_id: str,
    conversation_id: str,
    *,
    process_now: bool,
    repository: ProfileRepository = profile_repository,
    model_factory=get_llm,
) -> dict[str, Any] | None:
    """Mark a switch/new/logout boundary; logout passes process_now=False."""

    state = repository.mark_conversation_extraction_pending(
        user_id, conversation_id
    )
    if not state or not process_now:
        return state
    return ConversationMemoryExtractor(repository, model_factory).extract(
        user_id, conversation_id
    )
