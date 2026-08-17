from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator

from app.state.schema import ProfileFacts

from app.database.repository import ProfileRepository, profile_repository
from app.llm.model import get_llm
from app.services.token_accounting import heuristic_text_tokens


class ExtractedMemoryProposal(BaseModel):
    destination: Literal["profile", "semantic_memory", "episodic_memory", "none"]
    source_message_id: str | None = None
    evidence_text: str = ""
    evidence_start: int = 0
    evidence_end: int = 0
    confidence: float | None = None
    operation_hint: Literal["add", "update", "remove", "noop", "possible_conflict"] = "add"
    self_referential: bool = False
    profile_field: str | None = None
    semantic_group: str | None = None
    topic_key: str | None = None
    value: Any = None
    content: str | None = None
    event_status: Literal["completed", "current", "planned", "unknown"] | None = None
    event_time: datetime | None = None
    raw_temporal_expression: str | None = None
    proposal_sources: list[Literal["llm", "deterministic"]] = Field(default_factory=lambda: ["llm"])

    @field_validator("semantic_group", "topic_key", mode="before")
    @classmethod
    def normalize_keys(cls, value: Any) -> str | None:
        return normalize_topic_key(value) if value else None


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
    """Build a bounded segment that gives every user-authored turn priority."""

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
        user_indexes = [index for index, item in enumerate(segment) if item["role"] == "user"]
        selected_indexes: set[int] = set()
        # User turns are considered chronologically and never require a regex hit.
        for index in user_indexes:
            trial = [segment[item] for item in sorted([*selected_indexes, index])]
            if heuristic_text_tokens(json.dumps(trial, default=str)) <= max_tokens:
                selected_indexes.add(index)
        # Spend remaining budget on nearby assistant context, nearest to user turns.
        assistant_indexes = [index for index, item in enumerate(segment) if item["role"] == "assistant"]
        for index in sorted(assistant_indexes, key=lambda i: min((abs(i - u) for u in user_indexes), default=i)):
            trial = [segment[item] for item in sorted([*selected_indexes, index])]
            if heuristic_text_tokens(json.dumps(trial, default=str)) <= max_tokens:
                selected_indexes.add(index)
        selected = [segment[index] for index in sorted(selected_indexes)]
        mode = "user_prioritized_context"
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


TOPIC_ALIASES = {
    "remote_work": "work_mode", "work_modality": "work_mode",
    "remote_vs_onsite": "work_mode", "onsite_work": "work_mode",
}


def normalize_topic_key(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").casefold().strip().replace("-", "_"))
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return TOPIC_ALIASES.get(normalized, normalized)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _signal_topic(category: str, values: list[str]) -> str:
    text = " ".join(values).casefold()
    if category == "preference" and any(term in text for term in ("remote", "onsite", "on-site", "hybrid")):
        return "work_mode"
    return ""


def _deterministic_proposals(payload: dict[str, Any]) -> list[ExtractedMemoryProposal]:
    by_message = {item["message_id"]: item for item in payload["messages"]}
    proposals = []
    for signal_type, signals in payload["signals_grouped_chronologically"].items():
        latest = signals[-1]
        message_id = latest["source_message_id"]
        source = by_message.get(message_id, {})
        evidence = str(source.get("content") or "")
        operation = {"replace": "update", "remove": "remove"}.get(latest["operation_hint"], "add")
        if signal_type.startswith("profile."):
            proposals.append(ExtractedMemoryProposal(
                destination="profile", profile_field=signal_type.split(".", 1)[1],
                operation_hint=operation, value=latest["value_hint"],
                source_message_id=message_id, evidence_text=evidence,
                evidence_start=0, evidence_end=len(evidence), self_referential=True,
                proposal_sources=["deterministic"],
            ))
        else:
            category = signal_type.split(".", 1)[1]
            event_time = None
            raw_temporal = None
            if category == "event":
                source = by_message.get(latest["source_message_id"], {})
                event_time, raw_temporal = _event_time(latest["value_hint"], source.get("created_at"))
            content = "; ".join(latest["value_hint"])
            common = dict(source_message_id=message_id, evidence_text=evidence,
                          evidence_start=0, evidence_end=len(evidence), self_referential=True,
                          operation_hint=operation, proposal_sources=["deterministic"])
            if category == "event":
                status = "planned" if re.search(r"\b(?:plan|will|next)\b", evidence, re.I) else "unknown"
                proposals.append(ExtractedMemoryProposal(
                    destination="episodic_memory", content=content, event_status=status,
                    event_time=event_time, raw_temporal_expression=raw_temporal, **common,
                ))
            else:
                proposals.append(ExtractedMemoryProposal(
                    destination="semantic_memory", semantic_group=category,
                    topic_key=_signal_topic(category, latest["value_hint"]), value=content, **common,
                ))
    return proposals


def _validated_extraction_proposals(
    payload: dict[str, Any], proposals: list[ExtractedMemoryProposal]
) -> list[ExtractedMemoryProposal]:
    """Merge both sources, then enforce exact evidence, ownership, and schema."""

    messages = {item["message_id"]: item for item in payload["messages"]}
    canonical_fields = set(ProfileFacts.model_fields)
    accepted: list[ExtractedMemoryProposal] = []
    merged: list[ExtractedMemoryProposal] = []
    for proposal in [*proposals, *_deterministic_proposals(payload)]:
        normalized_value = _normalized_text(str(proposal.value or proposal.content or "")).casefold()
        duplicate = next((item for item in merged if (
            item.source_message_id == proposal.source_message_id
            and item.destination == proposal.destination
            and (item.profile_field == proposal.profile_field)
            and (not item.semantic_group or not proposal.semantic_group or item.semantic_group == proposal.semantic_group)
            and _normalized_text(str(item.value or item.content or "")).casefold() == normalized_value
            and (
                item.evidence_start <= proposal.evidence_end
                and proposal.evidence_start <= item.evidence_end
                or _normalized_text(item.evidence_text).casefold() == _normalized_text(proposal.evidence_text).casefold()
            )
        )), None)
        if duplicate:
            duplicate.proposal_sources = list(dict.fromkeys([*duplicate.proposal_sources, *proposal.proposal_sources]))
            if "llm" in proposal.proposal_sources:
                richer = proposal.model_copy(deep=True)
                richer.proposal_sources = duplicate.proposal_sources
                merged[merged.index(duplicate)] = richer
            continue
        merged.append(proposal)
    for proposal in merged:
        if proposal.destination == "none":
            continue
        message = messages.get(proposal.source_message_id or "")
        if not message or message.get("role") != "user":
            continue
        original = str(message.get("content") or "")
        if not (0 <= proposal.evidence_start <= proposal.evidence_end <= len(original)):
            continue
        if _normalized_text(original[proposal.evidence_start:proposal.evidence_end]) != _normalized_text(proposal.evidence_text):
            continue
        evidence = proposal.evidence_text
        third_party = bool(re.search(r"\b(?:my friend|my (?:sister|brother|manager)|he|she|they|their)\b", evidence, re.I))
        first_person = bool(re.search(r"\b(?:I|I'm|I've|I'd|my|mine|me)\b", evidence, re.I))
        if third_party or (not first_person and not proposal.self_referential):
            continue
        if proposal.destination == "profile":
            if proposal.profile_field not in canonical_fields:
                continue
            try:
                ProfileFacts.model_validate({proposal.profile_field: proposal.value})
            except Exception:
                continue
        elif proposal.destination == "semantic_memory":
            proposal.semantic_group = normalize_topic_key(proposal.semantic_group)
            proposal.topic_key = normalize_topic_key(proposal.topic_key)
            if not proposal.semantic_group or proposal.value in (None, "", []):
                continue
            if not proposal.topic_key:
                if proposal.proposal_sources != ["deterministic"]:
                    continue
                proposal.operation_hint = "add"
        elif proposal.destination == "episodic_memory":
            if not str(proposal.content or "").strip():
                continue
            proposal.event_status = proposal.event_status or "unknown"
            if proposal.raw_temporal_expression and _normalized_text(proposal.raw_temporal_expression).casefold() not in _normalized_text(evidence).casefold():
                continue
            if proposal.event_time and not proposal.raw_temporal_expression:
                continue
        key = (
            proposal.destination, proposal.profile_field, proposal.semantic_group,
            proposal.topic_key, _normalized_text(str(proposal.value or proposal.content)).casefold(),
            proposal.operation_hint,
        )
        if not any(getattr(item, "_dedupe_key", None) == key for item in accepted):
            object.__setattr__(proposal, "_dedupe_key", key)
            accepted.append(proposal)
    return accepted


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
            self._persist_proposals(
                user_id,
                conversation_id,
                run["extraction_run_id"],
                payload,
                _validated_extraction_proposals(payload, proposals),
            )
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
            if proposal.destination == "profile" and proposal.profile_field:
                before = profile.get(proposal.profile_field)
                raw_values = proposal.value if isinstance(proposal.value, list) else [proposal.value]
                raw_values = [value for value in raw_values if value not in (None, "")]
                proposed: Any = raw_values
                if proposal.profile_field not in {"skills", "projects", "experience", "education", "courses", "achievements", "certifications", "target_roles", "preferred_locations", "employment_types"}:
                    proposed = raw_values[-1] if raw_values else None
                    if proposal.profile_field == "graduation_year" and str(proposed).isdigit():
                        proposed = int(proposed)
                elif proposal.operation_hint == "add":
                    current = list(before or [])
                    existing = {str(item).casefold() for item in current}
                    proposed = [*current, *(value for value in raw_values if str(value).casefold() not in existing)]
                elif proposal.operation_hint == "remove":
                    removed = {str(value).casefold() for value in raw_values}
                    proposed = [item for item in list(before or []) if str(item).casefold() not in removed]
                profile_changes.append({
                    "field_key": proposal.profile_field, "operation": proposal.operation_hint,
                    "before_value": before, "proposed_value": proposed,
                    "source": {"extraction_run_id": run_id, "message_ids": [proposal.source_message_id],
                               "evidence_text": proposal.evidence_text,
                               "evidence_start": proposal.evidence_start, "evidence_end": proposal.evidence_end},
                })
            elif proposal.destination in {"semantic_memory", "episodic_memory"}:
                self._persist_memory_candidate(user_id, conversation_id, run_id, proposal)
        if profile_changes:
            self.repository.create_profile_revision_draft(
                user_id, source_type="conversation",
                source_conversation_id=conversation_id,
                source_message_ids=sorted({item for change in profile_changes for item in change["source"]["message_ids"]}),
                changes=profile_changes,
            )

    def _persist_memory_candidate(self, user_id: str, conversation_id: str, run_id: str, proposal: ExtractedMemoryProposal) -> None:
        content = str(proposal.content if proposal.destination == "episodic_memory" else proposal.value or "").strip()
        if not content:
            return
        category = "event" if proposal.destination == "episodic_memory" else str(proposal.semantic_group)
        semantic_rows = self.repository.list_semantic_memories(user_id) if proposal.destination == "semantic_memory" else []
        same_topic = [item for item in semantic_rows if proposal.topic_key and item.get("topic_key") == proposal.topic_key]
        active = [item for item in self.repository.list_memories(user_id) if item["category"] == category]
        normalized = " ".join(content.casefold().split())
        exact = next((item for item in same_topic if " ".join(str(item["value"]).casefold().split()) == normalized), None)
        if exact is None and proposal.destination == "episodic_memory":
            exact = next((item for item in self.repository.list_career_events(user_id) if " ".join(item["content"].casefold().split()) == normalized), None)
        if exact is None and proposal.proposal_sources == ["deterministic"]:
            exact = next((item for item in active if " ".join(item["content"].casefold().split()) == normalized), None)
        if exact and proposal.operation_hint != "remove":
            return  # NOOP remains visible in the extraction audit, not review UI.
        # Until structured durable rows are introduced in Phase B, never select the
        # first item in a broad category. Only exact legacy rows are safe targets.
        source_text = proposal.evidence_text.casefold()
        replacement = any(term in source_text for term in ("actually", "instead", "no longer", "now prefer", "changed my mind"))
        existing = exact or (same_topic[0] if same_topic and replacement else None)
        if proposal.operation_hint == "remove" and existing:
            operation = "REVOKE"
        elif existing and replacement:
            operation = "UPDATE"
        elif same_topic and proposal.topic_key == "work_mode":
            existing = same_topic[0]
            operation = "CONFLICT"
        else:
            operation = "ADD"
        self.repository.create_memory_candidate(
            user_id, category=category, content=content, confidence=proposal.confidence,
            source="conversation_extraction", operation=operation,
            existing_memory_id=(existing["memory_id"] if existing and not existing.get("semantic_memory_id") and not existing.get("career_event_id") else None),
            existing_entity_id=(existing.get("semantic_memory_id") or existing.get("career_event_id")) if existing else None,
            source_conversation_id=conversation_id,
            source_message_ids=[proposal.source_message_id] if proposal.source_message_id else [],
            extraction_run_id=run_id, event_time=proposal.event_time,
            raw_temporal_expression=proposal.raw_temporal_expression,
            memory_kind="episodic" if proposal.destination == "episodic_memory" else "semantic",
            semantic_group=proposal.semantic_group,
            topic_key=proposal.topic_key,
            proposed_value=proposal.value,
            event_status=proposal.event_status,
            evidence_text=proposal.evidence_text,
            evidence_start=proposal.evidence_start,
            evidence_end=proposal.evidence_end,
            proposal_sources=proposal.proposal_sources,
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
