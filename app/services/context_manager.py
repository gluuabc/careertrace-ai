from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.database.repository import ProfileRepository, profile_repository
from app.llm.model import get_llm
from app.prompts import ROUTING_SYSTEM_PROMPT, build_system_prompt
from app.services.skill_registry import SkillRegistry, skill_registry
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval import HybridRetrievalService
from app.services.token_accounting import heuristic_input_tokens, heuristic_text_tokens
from app.services.token_accounting import ModelCallObserver
from app.services.memory_retrieval import ProgressiveMemoryService


def estimate_tokens(text: str) -> int:
    return heuristic_text_tokens(text)


def estimate_bound_tool_schema_tokens() -> int:
    from app.tools import CAREER_AGENT_TOOLS
    schemas = [tool.tool_call_schema.model_json_schema() for tool in CAREER_AGENT_TOOLS]
    return estimate_tokens(json.dumps(schemas, sort_keys=True, default=str))


class ContextManager:
    def __init__(
        self,
        repository: ProfileRepository = profile_repository,
        registry: SkillRegistry = skill_registry,
        retrieval_repository: RetrievalRepository | None = None,
        retrieval_service: HybridRetrievalService | None = None,
        memory_service: ProgressiveMemoryService | None = None,
    ):
        self.repository = repository
        self.registry = registry
        self.retrieval_repository = retrieval_repository or RetrievalRepository(repository.session_factory)
        self.retrieval_service = retrieval_service or HybridRetrievalService(self.retrieval_repository)
        self.memory_service = memory_service or ProgressiveMemoryService(
            repository, self.retrieval_service
        )

    @staticmethod
    def model_context_limit() -> int:
        configured = os.getenv("CONTEXT_MODEL_LIMIT_TOKENS", "").strip()
        if configured:
            return int(configured)
        model = os.getenv("BEDROCK_MODEL_REASONING", "").casefold()
        known = {
            "claude-sonnet-4": 200_000,
            "claude-3-7-sonnet": 200_000,
            "nova-lite": 300_000,
        }
        return next((limit for name, limit in known.items() if name in model), 32_000)

    @classmethod
    def compression_threshold(cls) -> int:
        usable = (
            cls.model_context_limit()
            - int(os.getenv("CONTEXT_RESERVED_OUTPUT_TOKENS", "4096"))
            - int(os.getenv("CONTEXT_SAFETY_MARGIN_TOKENS", "8192"))
        )
        ratio = float(os.getenv("CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.75"))
        return max(1000, int(usable * ratio))

    def runtime_block(
        self,
        *,
        user_id: str,
        conversation_summary: str | None,
        current_task: dict[str, Any],
        selected_entities: dict[str, Any],
        loaded_skills: dict[str, str],
        agent_status: dict[str, Any],
        conversation_id: str | None = None,
        memory_query: str = "",
        reference_sink: dict[str, Any] | None = None,
    ) -> str:
        intent = current_task.get("intent") or current_task.get("workflow") or "concise_guidance"
        progressive = self.memory_service.build_context(
            user_id=user_id,
            conversation_id=conversation_id,
            intent=intent,
            query=memory_query,
            include_source_context=any(
                term in memory_query.casefold()
                for term in ("why did", "when did", "source", "provenance", "conflict", "previous context")
            ),
        )
        overlay = progressive["current_conversation_overlay"]
        if reference_sink is not None:
            reference_sink.clear()
            reference_sink.update(progressive["personalization_references"])
        payload = {
            "current_time": datetime.now(timezone.utc).isoformat(),
            "relevant_profile_fields": progressive["profile"],
            "current_conversation_profile_overlay": {
                "signals": overlay["signals"],
                "precedence": "latest current-conversation explicit statement wins",
            },
            "selected_approved_memories": progressive["memory_details"],
            "current_conversation_memory_overlay": overlay[
                "current_thread_memories"
            ],
            "memory_source_context": progressive["memory_source_context"],
            "conversation_summary": conversation_summary,
            "current_task": current_task,
            "selected_entities": selected_entities,
            "loaded_skills": loaded_skills,
            "agent_status": agent_status,
        }
        return "<runtime_context>\n" + escape(
            json.dumps(payload, ensure_ascii=False, default=str, indent=2),
            quote=False,
        ) + "\n</runtime_context>"

    def build_routing_messages(
        self,
        *,
        user_id: str,
        conversation_id: str,
        current_request: str,
        active_workflow: str | None = None,
        selected_entities: dict[str, Any] | None = None,
    ) -> list[BaseMessage]:
        """Build the small, routing-only view without profile, memories, or Skills."""

        conversation = self.repository.get_conversation(user_id, conversation_id)
        summary = self.repository.get_latest_context_summary(user_id, conversation_id)
        history = list(conversation["messages"])
        if (
            history
            and history[-1]["role"] == "user"
            and history[-1]["content"] == current_request
        ):
            history.pop()
        if summary:
            boundary = summary["covered_through_message_id"]
            boundary_index = next(
                (
                    index
                    for index, item in enumerate(history)
                    if item["message_id"] == boundary
                ),
                -1,
            )
            if boundary_index >= 0:
                history = history[boundary_index + 1 :]

        messages: list[BaseMessage] = [SystemMessage(content=ROUTING_SYSTEM_PROMPT)]
        if summary:
            messages.append(
                HumanMessage(
                    content=(
                        "<persisted_conversation_summary>\n"
                        + summary["summary"]
                        + "\n</persisted_conversation_summary>"
                    )
                )
            )
        for item in history:
            messages.append(
                HumanMessage(content=item["content"])
                if item["role"] == "user"
                else AIMessage(content=item["content"])
            )
        routing_state: dict[str, Any] = {}
        if active_workflow:
            routing_state["active_workflow"] = active_workflow
        if selected_entities:
            routing_state["selected_entities"] = selected_entities
        if routing_state:
            messages.append(
                HumanMessage(
                    content="<routing_state>\n"
                    + json.dumps(routing_state, ensure_ascii=False, default=str)
                    + "\n</routing_state>"
                )
            )
        messages.append(HumanMessage(content=current_request))
        return messages

    def _plan_context(
        self,
        *,
        workflow: str,
        current_request: str,
        routing_messages: list[BaseMessage],
        profile: dict[str, Any] | None,
        skill: str,
        selected: dict[str, Any],
    ) -> list[BaseMessage]:
        routing_tail = [
            {"role": message.type, "content": str(message.content)}
            for message in routing_messages[1:-1]
        ]
        payload = {
            "workflow": workflow,
            "request": current_request,
            "routing_conversation_context": routing_tail,
            "confirmed_profile": profile,
            "selected_context": selected,
            "skill": skill,
        }
        return [
            HumanMessage(
                content=(
                    "Create only the structured arguments required for the controlled "
                    "initial tool. Do not choose a tool or invent missing external facts.\n"
                    + json.dumps(payload, ensure_ascii=False, default=str, indent=2)
                )
            )
        ]

    def build_job_plan_context(self, **kwargs: Any) -> list[BaseMessage]:
        return self._plan_context(workflow="job_search", **kwargs)

    def build_people_plan_context(self, **kwargs: Any) -> list[BaseMessage]:
        return self._plan_context(workflow="people_search", **kwargs)

    def build_resume_plan_context(self, **kwargs: Any) -> list[BaseMessage]:
        return self._plan_context(workflow="resume_revision", **kwargs)

    def build_outreach_plan_context(self, **kwargs: Any) -> list[BaseMessage]:
        return self._plan_context(workflow="outreach", **kwargs)

    def build_messages(
        self,
        *,
        user_id: str,
        conversation_id: str,
        current_request: str,
        current_task: dict[str, Any] | None = None,
        selected_entities: dict[str, Any] | None = None,
        loaded_skills: dict[str, str] | None = None,
        agent_status: dict[str, Any] | None = None,
        run_id: str | None = None,
        reference_sink: dict[str, Any] | None = None,
    ) -> list[BaseMessage]:
        conversation = self.repository.get_conversation(user_id, conversation_id)
        summary = self.repository.get_latest_context_summary(user_id, conversation_id)
        history = list(conversation["messages"])
        if history and history[-1]["role"] == "user" and history[-1]["content"] == current_request:
            current_message = history.pop()
        else:
            current_message = {"role": "user", "content": current_request}
        if summary:
            boundary = summary["covered_through_message_id"]
            boundary_index = next(
                (index for index, item in enumerate(history) if item["message_id"] == boundary),
                -1,
            )
            if boundary_index >= 0:
                history = history[boundary_index + 1 :]
        runtime = self.runtime_block(
            user_id=user_id,
            conversation_id=conversation_id,
            conversation_summary=(
                (
                    summary["summary"]
                    + ("\nEvidence IDs: " + ", ".join(summary["evidence_ids"]) if summary["evidence_ids"] else "")
                )
                if summary
                else None
            ),
            current_task=current_task or {},
            selected_entities=selected_entities or {},
            loaded_skills=loaded_skills or {},
            agent_status=agent_status or {},
            memory_query=current_request,
            reference_sink=reference_sink,
        )
        messages: list[BaseMessage] = [
            SystemMessage(content=build_system_prompt(self.registry.catalog())),
            SystemMessage(content=runtime),
        ]
        for item in history:
            messages.append(
                HumanMessage(content=item["content"])
                if item["role"] == "user"
                else AIMessage(content=item["content"])
            )
        messages.append(HumanMessage(content=current_message["content"]))
        return self._compress_if_needed(
            user_id=user_id,
            conversation_id=conversation_id,
            persisted_messages=history,
            messages=messages,
            current_request=current_request,
            current_task=current_task or {},
            selected_entities=selected_entities or {},
            loaded_skills=loaded_skills or {},
            agent_status=agent_status or {},
            run_id=run_id,
            reference_sink=reference_sink,
        )

    def _compress_if_needed(
        self,
        *,
        user_id: str,
        conversation_id: str,
        persisted_messages: list[dict[str, Any]],
        messages: list[BaseMessage],
        current_request: str,
        current_task: dict[str, Any],
        selected_entities: dict[str, Any],
        loaded_skills: dict[str, str],
        agent_status: dict[str, Any],
        run_id: str | None,
        reference_sink: dict[str, Any] | None,
    ) -> list[BaseMessage]:
        total = sum(estimate_tokens(str(message.content)) for message in messages)
        total += estimate_bound_tool_schema_tokens()
        if total <= self.compression_threshold() or len(persisted_messages) < 4:
            return messages
        batch = persisted_messages[: max(2, len(persisted_messages) // 2)]
        prompt = (
            "Summarize this older conversation segment for the current career task. "
            "Preserve confirmed facts, decisions, constraints, unresolved questions, "
            "errors, approval decisions, and every [EVIDENCE: ...] identifier.\n\n"
            f"CURRENT REQUEST: {current_request}\nCURRENT TASK: {json.dumps(current_task)}\n"
            f"STATUS: {json.dumps(agent_status)}\nSEGMENT:\n{json.dumps(batch)}"
        )
        try:
            response = ModelCallObserver(self.repository).invoke(
                get_llm("cheap"), [HumanMessage(content=prompt)],
                user_id=user_id, conversation_id=conversation_id, run_id=run_id,
                stage="context_compression", model_type="cheap",
                model_id=os.getenv("BEDROCK_MODEL_CHEAP", ""), compression_triggered=True,
            )
            summary_text = str(response.content).strip()
            if not summary_text:
                raise ValueError("Empty compression response")
            strategy = "query_aware_llm"
        except Exception:
            summary_text = (
                "[CONTEXT TRUNCATED DUE TO COMPRESSION FAILURE]\n"
                + "\n".join(item["content"][:500] for item in batch)
            )
            strategy = "explicit_truncation_fallback"
        evidence_ids = sorted(
            {
                token.split("]", 1)[0]
                for item in batch
                for token in item["content"].split("[EVIDENCE: ")[1:]
                if "]" in token
            }
        )
        self.repository.save_context_summary(
            user_id,
            conversation_id,
            summary=summary_text,
            covered_through_message_id=batch[-1]["message_id"],
            evidence_ids=evidence_ids,
            strategy=strategy,
        )
        return self.build_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            current_request=current_request,
            current_task=current_task,
            selected_entities=selected_entities,
            loaded_skills=loaded_skills,
            agent_status=agent_status,
            run_id=run_id,
            reference_sink=reference_sink,
        )

    def final_preflight_messages(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], int, bool]:
        """Apply a final bounded compaction after current-run ToolMessages are appended."""
        from app.tools import CAREER_AGENT_TOOLS

        total = heuristic_input_tokens(messages, tools=list(CAREER_AGENT_TOOLS))
        if total <= self.compression_threshold():
            return messages, total, False
        # Preserve both system messages and the latest tool exchange. Older current-run
        # tool data is replaced with metadata-only summaries; source text remains in evidence.
        compacted: list[BaseMessage] = []
        for index, message in enumerate(messages):
            if message.type == "tool" and index < len(messages) - 2:
                compacted.append(type(message)(content="[Earlier tool result compacted; use authorized evidence read if needed.]", tool_call_id=getattr(message, "tool_call_id", "compacted"), name=getattr(message, "name", None)))
            else:
                compacted.append(message)
        return compacted, heuristic_input_tokens(compacted, tools=list(CAREER_AGENT_TOOLS)), True


context_manager = ContextManager()
