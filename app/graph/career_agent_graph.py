from __future__ import annotations

import json
import os
import re
from typing import Any
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from app.database.repository import ProfileRepository, profile_repository
from app.graph.checkpoint import get_default_checkpointer
from app.llm.model import get_llm
from app.services.context_manager import ContextManager, context_manager
from app.services.errors import safe_provider_message, sanitize_diagnostic
from app.services.memory_signals import detect_memory_signals, merge_memory_signals
from app.services.skill_registry import SkillRegistry, skill_registry
from app.services.trajectory import TrajectoryRecorder
from app.services.token_accounting import ModelCallObserver
from app.state.agent_schema import (
    AgentStatus,
    AgentTodoItem,
    CareerAgentState,
    CareerIntent,
    IntentDecision,
    JobSearchRequest,
    OutreachDraftInput,
    PeopleSearchRequest,
    ResumeRevisionDraftInput,
)
from app.tools import CAREER_AGENT_TOOLS

ACTION_INTENTS = {
    CareerIntent.JOB_SEARCH,
    CareerIntent.PEOPLE_SEARCH,
    CareerIntent.RESUME_REVISION,
    CareerIntent.OUTREACH,
}
INTENT_SKILLS = {
    CareerIntent.JOB_SEARCH: "job_search",
    CareerIntent.PEOPLE_SEARCH: "people_search",
    CareerIntent.RESUME_REVISION: "resume_revision",
    CareerIntent.OUTREACH: "outreach",
}
TOOL_BY_INTENT = {
    CareerIntent.JOB_SEARCH: ("search_jobs", JobSearchRequest, "request"),
    CareerIntent.PEOPLE_SEARCH: ("search_people", PeopleSearchRequest, "request"),
    CareerIntent.RESUME_REVISION: (
        "save_resume_revision_draft",
        ResumeRevisionDraftInput,
        "draft",
    ),
    CareerIntent.OUTREACH: ("save_outreach_draft", OutreachDraftInput, "draft"),
}
ALLOWED_TOOLS_BY_INTENT = {
    CareerIntent.JOB_SEARCH: {
        "search_jobs", "get_job_details", "read_skill_file", "read_evidence"
    },
    CareerIntent.PEOPLE_SEARCH: {
        "search_people", "get_person_details", "read_skill_file", "read_evidence"
    },
    CareerIntent.RESUME_REVISION: {
        "get_job_details", "read_evidence", "read_skill_file",
        "save_resume_revision_draft",
    },
    CareerIntent.OUTREACH: {
        "get_person_details", "read_evidence", "read_skill_file",
        "save_outreach_draft", "update_outreach_status",
    },
}
SOURCE_TOOLS = {"search_jobs", "search_people"}


def parse_structured_job_request(
    request: str, profile: dict[str, Any] | None
) -> JobSearchRequest | None:
    """Use a deterministic fast path only for clearly structured job searches."""

    text = " ".join(request.strip().split())
    lowered = text.casefold()
    count_match = re.search(r"\b(?:find|show|give me)\s+(\d{1,2})\b", lowered)
    location_match = re.search(r"\bin\s+([A-Za-z][A-Za-z .-]{1,50})[.!?]?$", text)
    employment = ["Internship"] if "intern" in lowered else []
    if not count_match or not location_match or not employment:
        return None
    role_text = re.sub(r"^(?:find|show|give me)\s+\d{1,2}\s+", "", text, flags=re.I)
    role_text = re.sub(r"\s+in\s+[A-Za-z][A-Za-z .-]{1,50}[.!?]?$", "", role_text, flags=re.I)
    role_text = re.sub(r"\b(internships?|roles?|jobs?)\b", "", role_text, flags=re.I).strip()
    if not role_text:
        return None
    profile = profile or {}
    return JobSearchRequest(
        target_roles=[role_text],
        locations=[location_match.group(1).strip().rstrip(".!?")],
        employment_types=employment,
        graduation_year=profile.get("graduation_year"),
        work_authorization_requirement=profile.get("work_authorization"),
        profile_skills=list(profile.get("skills") or []),
        requested_count=min(int(count_match.group(1)), 10),
        max_results=10,
    )


def _fallback_intent(request: str) -> IntentDecision:
    memory_signals = detect_memory_signals(request)
    value = request.casefold()
    if any(term in value for term in ("find job", "search job", "opening", "internship")):
        intent = CareerIntent.JOB_SEARCH
    elif "resume" in value and any(
        term in value for term in ("revise", "improve", "tailor", "rewrite", "edit")
    ):
        intent = CareerIntent.RESUME_REVISION
    elif any(term in value for term in ("outreach", "draft message", "email professor", "message recruiter", "follow up")):
        intent = CareerIntent.OUTREACH
    elif any(
        term in value
        for term in (
            "alumni",
            "alumnus",
            "alumna",
            "professor",
            "recruiter",
            "people search",
        )
    ):
        intent = CareerIntent.PEOPLE_SEARCH
    elif any(term in value for term in ("career plan", "career timeline", "action plan")):
        intent = CareerIntent.ACTION_PLAN
    elif any(
        term in value
        for term in (
            "what role",
            "which role",
            "career advice",
            "career guidance",
            "what should i prioritize",
        )
    ):
        intent = CareerIntent.CONCISE_GUIDANCE
    else:
        return IntentDecision(
            intent=CareerIntent.CLARIFICATION,
            goal=request.strip(),
            needs_user_input=True,
            clarification_question=(
                "Would you like career guidance, a job search, a people search, "
                "a resume revision, or an outreach draft?"
            ),
            memory_signals=memory_signals,
        )
    return IntentDecision(
        intent=intent, goal=request.strip(), memory_signals=memory_signals
    )


class CareerAgentGraph:
    def __init__(
        self,
        repository: ProfileRepository = profile_repository,
        context: ContextManager = context_manager,
        registry: SkillRegistry = skill_registry,
        checkpointer=None,
        model_factory: Callable[[str], Any] = get_llm,
    ):
        self.repository = repository
        self.context = context
        self.registry = registry
        self.model_factory = model_factory
        self.tool_node = ToolNode(CAREER_AGENT_TOOLS, handle_tool_errors=True)
        self.graph = self._build(checkpointer)

    @staticmethod
    def _model_id(model_type: str) -> str:
        key = "BEDROCK_MODEL_CHEAP" if model_type == "cheap" else "BEDROCK_MODEL_REASONING"
        return os.getenv(key, "")

    def _invoke_observed(self, runnable: Any, messages: list[Any], state: CareerAgentState, *, stage: str, model_type: str, tools: list[Any] | None = None, compression_triggered: bool = False) -> Any:
        return ModelCallObserver(self.repository).invoke(
            runnable, messages,
            user_id=state["user_id"], conversation_id=state.get("conversation_id"),
            run_id=state.get("run_id"), stage=stage, model_type=model_type,
            model_id=self._model_id(model_type), tools=tools,
            compression_triggered=compression_triggered,
        )

    def _build(self, checkpointer=None):
        graph = StateGraph(CareerAgentState)
        graph.add_node("initialize_run", self.initialize_run)
        graph.add_node("classify_intent", self.classify_intent)
        graph.add_node("prepare_workflow", self.prepare_workflow)
        graph.add_node("plan_action", self.plan_action)
        graph.add_node("agent_model", self.agent_model)
        graph.add_node("execute_tools", self.execute_tools)
        graph.add_node("finalize", self.finalize)
        graph.add_edge(START, "initialize_run")
        graph.add_edge("initialize_run", "classify_intent")
        graph.add_conditional_edges(
            "classify_intent",
            self._route_after_classify,
            {"clarify": "finalize", "prepare": "prepare_workflow"},
        )
        graph.add_conditional_edges(
            "prepare_workflow",
            self._route_after_prepare,
            {"action": "plan_action", "respond": "agent_model"},
        )
        graph.add_conditional_edges(
            "plan_action",
            lambda state: "tools" if state.get("messages") and isinstance(state["messages"][-1], AIMessage) and state["messages"][-1].tool_calls else "final",
            {"tools": "execute_tools", "final": "finalize"},
        )
        graph.add_conditional_edges(
            "execute_tools",
            self._route_after_tools,
            {"continue": "agent_model", "final": "finalize"},
        )
        graph.add_conditional_edges(
            "agent_model",
            self._route_after_model,
            {"tools": "execute_tools", "final": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)

    def initialize_run(self, state: CareerAgentState) -> dict[str, Any]:
        request = str(state.get("current_request") or "").strip()
        run = self.repository.create_agent_run(
            state["user_id"],
            state["conversation_id"],
            goal=request,
            user_message_id=state.get("user_message_id"),
        )
        status = AgentStatus(goal=request, workflow_stage="classifying_intent", current_step="Classify request")
        return {
            "run_id": run["run_id"],
            "workflow_stage": "classifying_intent",
            "iteration": 0,
            "total_source_calls": 0,
            "consecutive_no_new_results": 0,
            "tool_call_counts": {},
            "warnings": [],
            "job_candidates": [],
            "people_candidates": [],
            "evidence_ids": [],
            "loaded_skills": {},
            "status": status.model_dump(mode="json"),
            "stop_after_tools": False,
            "partial_result": False,
        }

    def classify_intent(self, state: CareerAgentState) -> dict[str, Any]:
        request = state["current_request"]
        routing_messages = self.context.build_routing_messages(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            current_request=request,
            active_workflow=str(state.get("intent") or "") or None,
            selected_entities={
                "job_ids": state.get("selected_job_ids", []),
                "person_ids": state.get("selected_people_ids", []),
            }
            if state.get("selected_job_ids") or state.get("selected_people_ids")
            else None,
        )
        classifier = self.model_factory("cheap").with_structured_output(IntentDecision)
        decision: IntentDecision | None = None
        diagnostic = ""
        routing_source = "llm"
        for attempt in range(2):
            try:
                candidate = self._invoke_observed(
                    classifier, routing_messages, state,
                    stage="classify_intent_retry" if attempt else "classify_intent",
                    model_type="cheap",
                )
                decision = (
                    candidate
                    if isinstance(candidate, IntentDecision)
                    else IntentDecision.model_validate(candidate)
                )
                routing_source = "llm_retry" if attempt else "llm"
                break
            except Exception as error:
                diagnostic = sanitize_diagnostic(error)
        warnings = list(state.get("warnings", []))
        if decision is None:
            decision = _fallback_intent(request)
            routing_source = (
                "clarification_after_failure"
                if decision.intent == CareerIntent.CLARIFICATION
                else "deterministic_fallback"
            )
            warnings.append(
                "Intent classification was unavailable; CareerTrace used a safe "
                "deterministic route or requested clarification."
            )
            TrajectoryRecorder(
                state["user_id"], state["run_id"], self.repository
            ).step(
                "routing_warning",
                f"Structured classifier failed twice; routing_source={routing_source}; "
                f"diagnostic={diagnostic}",
                "completed",
            )
        decision.memory_signals = merge_memory_signals(
            decision.memory_signals, detect_memory_signals(request)
        )
        decision.memory_worthy = bool(decision.memory_signals)
        signal_payload = [
            item.model_dump(mode="json") for item in decision.memory_signals
        ]
        if signal_payload and state.get("user_message_id"):
            self.repository.record_conversation_memory_signals(
                state["user_id"],
                state["conversation_id"],
                state["user_message_id"],
                signal_payload,
            )
        self.repository.update_agent_run(
            state["user_id"],
            state["run_id"],
            intent=decision.intent.value,
            goal=decision.goal,
            status="needs_input" if decision.needs_user_input else "running",
        )
        TrajectoryRecorder(state["user_id"], state["run_id"], self.repository).step(
            "classify_intent",
            f"Routed request to {decision.intent.value}; routing_source={routing_source}.",
        )
        return {
            "intent": decision.intent,
            "current_goal": decision.goal,
            "needs_user_input": decision.needs_user_input,
            "final_response": decision.clarification_question or "",
            "workflow_stage": "preparing_workflow",
            "routing_source": routing_source,
            "memory_worthy": decision.memory_worthy,
            "memory_signals": signal_payload,
            "warnings": warnings,
        }

    def prepare_workflow(self, state: CareerAgentState) -> dict[str, Any]:
        intent = CareerIntent(state["intent"])
        skill_name = INTENT_SKILLS.get(intent)
        loaded: dict[str, str] = {}
        todo_content = {
            CareerIntent.JOB_SEARCH: ["Validate search requirements", "Search official sources", "Filter and present candidates"],
            CareerIntent.PEOPLE_SEARCH: ["Validate person target", "Search permitted sources", "Present evidence-backed matches"],
            CareerIntent.RESUME_REVISION: ["Load confirmed evidence", "Draft revisions", "Save unapplied draft"],
            CareerIntent.OUTREACH: ["Validate recipient and evidence", "Draft concise outreach", "Save unsent draft"],
        }.get(intent, ["Answer concisely", "Suggest one actionable next step"])
        todos = [AgentTodoItem(content=item, status="in_progress" if index == 0 else "pending") for index, item in enumerate(todo_content)]
        if skill_name:
            loaded[skill_name] = self.registry.read_skill(skill_name)
        status = AgentStatus(
            goal=state.get("current_goal") or state["current_request"],
            workflow_stage="planning" if intent in ACTION_INTENTS else "responding",
            current_step=todo_content[0],
            next_steps=todo_content[1:],
        )
        TrajectoryRecorder(state["user_id"], state["run_id"], self.repository).step(
            "prepare_workflow",
            f"Prepared {intent.value} workflow" + (f" with {skill_name} Skill." if skill_name else "."),
        )
        return {
            "active_skill": skill_name,
            "loaded_skills": loaded,
            "todo_items": [item.model_dump(mode="json") for item in todos],
            "status": status.model_dump(mode="json"),
            "workflow_stage": status.workflow_stage,
        }

    def plan_action(self, state: CareerAgentState) -> dict[str, Any]:
        intent = CareerIntent(state["intent"])
        tool_name, schema, argument_name = TOOL_BY_INTENT[intent]
        profile, profile_references, conversation_context = (
            self.context.memory_service.profile_projection(
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                intent=intent,
                query=state["current_request"],
            )
        )
        if conversation_context["current_thread_memories"]:
            profile = {
                **profile,
                "current_thread_memories": conversation_context[
                    "current_thread_memories"
                ],
            }
        routing_messages = self.context.build_routing_messages(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            current_request=state["current_request"],
            active_workflow=intent.value,
            selected_entities={
                "job_ids": state.get("selected_job_ids", []),
                "person_ids": state.get("selected_people_ids", []),
            },
        )
        selected: dict[str, Any] = {
            "selected_job_summaries": [
                item
                for item in state.get("job_candidates", [])
                if item.get("candidate_id") in state.get("selected_job_ids", [])
            ],
            "selected_people": [
                item
                for item in state.get("people_candidates", [])
                if item.get("candidate_id") in state.get("selected_people_ids", [])
            ],
        }
        if intent == CareerIntent.RESUME_REVISION:
            selected["documents"] = self.repository.list_documents(state["user_id"])
        if intent == CareerIntent.OUTREACH:
            selected["previous_drafts"] = self.repository.list_outreach_drafts(
                state["user_id"]
            )
        builder = {
            CareerIntent.JOB_SEARCH: self.context.build_job_plan_context,
            CareerIntent.PEOPLE_SEARCH: self.context.build_people_plan_context,
            CareerIntent.RESUME_REVISION: self.context.build_resume_plan_context,
            CareerIntent.OUTREACH: self.context.build_outreach_plan_context,
        }[intent]
        prompt = builder(
            current_request=state["current_request"],
            routing_messages=routing_messages,
            profile=profile,
            skill=state.get("loaded_skills", {}).get(
                state.get("active_skill") or "", ""
            ),
            selected=selected,
        )
        try:
            planned = (
                parse_structured_job_request(state["current_request"], profile)
                if intent == CareerIntent.JOB_SEARCH
                else None
            )
            if planned is None:
                planned = self._invoke_observed(
                    self.model_factory("reasoning").with_structured_output(schema),
                    prompt, state, stage="plan_action", model_type="reasoning",
                )
            if not isinstance(planned, schema):
                planned = schema.model_validate(planned)
            if isinstance(planned, JobSearchRequest):
                planned = planned.model_copy(
                    update={"profile_skills": list((profile or {}).get("skills") or [])}
                )
            call_id = f"call_{state['run_id']}_{state.get('iteration', 0) + 1}"
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": {argument_name: planned.model_dump(mode="json")},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
            references = dict(state.get("personalization_references") or {})
            references["profile"] = profile_references
            references.setdefault("approved_memories", [])
            return {
                "messages": [message],
                "workflow_stage": "executing_tools",
                "personalization_references": references,
            }
        except Exception as error:
            diagnostic = sanitize_diagnostic(error)
            response = (
                "I need one more specific target detail before I can safely prepare "
                "that workflow. Please clarify the role, person, document, or recipient."
            )
            return {
                "needs_user_input": True,
                "current_error": diagnostic,
                "final_response": response,
                "workflow_stage": "needs_input",
                "personalization_references": {
                    "profile": profile_references,
                    "approved_memories": [],
                },
            }

    def agent_model(self, state: CareerAgentState) -> dict[str, Any]:
        loaded_references: dict[str, Any] = {}
        messages = self.context.build_messages(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            current_request=state["current_request"],
            current_task={"intent": str(state.get("intent")), "goal": state.get("current_goal")},
            selected_entities={"job_ids": state.get("selected_job_ids", []), "people_ids": state.get("selected_people_ids", [])},
            loaded_skills=state.get("loaded_skills", {}),
            agent_status=state.get("status", {}),
            run_id=state.get("run_id"),
            reference_sink=loaded_references,
        )
        messages.extend(state.get("messages", []))
        messages, _final_tokens, compression_triggered = self.context.final_preflight_messages(messages)
        base_model = self.model_factory("reasoning")
        final_max_tokens = max(128, min(int(os.getenv("AGENT_FINAL_MAX_TOKENS", "384")), 2048))
        bounded_base_model = (
            base_model.model_copy(update={"max_tokens": final_max_tokens})
            if isinstance(base_model, BaseModel)
            else base_model
        )
        model = bounded_base_model.bind_tools(CAREER_AGENT_TOOLS)
        final_only_model = bounded_base_model
        no_tool_messages = [
            message
            for message in messages
            if not isinstance(message, ToolMessage)
            and not (isinstance(message, AIMessage) and message.tool_calls)
        ]
        no_tool_messages.append(
            HumanMessage(
                content=(
                    "Summarize the already collected structured results without calling tools. "
                    "Do not claim demo snapshots are current.\n"
                    + json.dumps(
                        {
                            "jobs": state.get("job_candidates", [])[:10],
                            "people": state.get("people_candidates", [])[:10],
                            "warnings": state.get("warnings", [])[:10],
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
            )
        )
        try:
            response = self._invoke_observed(
                model, messages, state, stage="agent_model", model_type="reasoning",
                tools=list(CAREER_AGENT_TOOLS), compression_triggered=compression_triggered,
            )
            if not isinstance(response, AIMessage):
                response = self._invoke_observed(
                    final_only_model, no_tool_messages, state, stage="agent_model_fallback",
                    model_type="reasoning", compression_triggered=compression_triggered,
                )
        except Exception as error:
            # Some Bedrock models can reject or emit an invalid ToolUse sequence even
            # when the preceding assistant/tool-result pair is valid. The bounded
            # no-tools fallback can still summarize already collected structured data;
            # it cannot initiate another source call.
            try:
                response = self._invoke_observed(
                    final_only_model,
                    no_tool_messages,
                    state,
                    stage="agent_model_fallback",
                    model_type="reasoning",
                    compression_triggered=compression_triggered,
                )
            except Exception:
                return {
                    "current_error": sanitize_diagnostic(error),
                    "final_response": safe_provider_message("the model step"),
                    "workflow_stage": "failed",
                    "personalization_references": self._merge_personalization_references(
                        state.get("personalization_references") or {}, loaded_references
                    ),
                }
        references = self._merge_personalization_references(
            state.get("personalization_references") or {}, loaded_references
        )
        return {
            "messages": [response],
            "iteration": state.get("iteration", 0) + 1,
            "workflow_stage": "reasoning",
            "personalization_references": references,
        }

    @staticmethod
    def _merge_personalization_references(
        current: dict[str, Any], loaded: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        merged: dict[str, list[dict[str, Any]]] = {}
        for key, identity in (
            ("profile", lambda item: (item.get("profile_version_id"), item.get("field"))),
            ("approved_memories", lambda item: item.get("memory_id")),
        ):
            values = []
            seen = set()
            for item in list(current.get(key) or []) + list(loaded.get(key) or []):
                marker = identity(item)
                if marker not in seen:
                    seen.add(marker)
                    values.append(dict(item))
            merged[key] = values
        return merged

    def execute_tools(self, state: CareerAgentState) -> dict[str, Any]:
        ai_message = state["messages"][-1]
        if not isinstance(ai_message, AIMessage):
            return {"current_error": "Tool execution requires an assistant tool call."}
        max_calls = int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12"))
        import time

        recorder = TrajectoryRecorder(state["user_id"], state["run_id"], self.repository)
        counts = dict(state.get("tool_call_counts", {}))
        source_calls = state.get("total_source_calls", 0)
        jobs = list(state.get("job_candidates", []))
        people = list(state.get("people_candidates", []))
        evidence_ids = list(state.get("evidence_ids", []))
        warnings = list(state.get("warnings", []))
        searched_jobs = False
        searched_people = False
        is_sufficient = bool(state.get("is_sufficient", False))
        intent = CareerIntent(state["intent"])
        allowed = ALLOWED_TOOLS_BY_INTENT.get(intent, set())
        tool_messages: list[ToolMessage] = []
        max_iterations = int(os.getenv("AGENT_MAX_ITERATIONS", "6"))
        stop_after_tools = False

        for call in ai_message.tool_calls:
            started = time.monotonic()
            rejection: dict[str, Any] | None = None
            if call["name"] not in allowed:
                rejection = {
                    "ok": False,
                    "error_type": "ToolNotAuthorized",
                    "error_message": "This tool is not authorized for the active workflow.",
                    "source_calls": 0,
                }
                warnings.append(
                    f"Rejected unauthorized tool {call['name']} for {intent.value}."
                )
            elif state.get("iteration", 0) >= max_iterations:
                rejection = {
                    "ok": False,
                    "error_type": "IterationBudgetExhausted",
                    "error_message": "The workflow iteration budget is exhausted.",
                    "source_calls": 0,
                }
                warnings.append("Workflow iteration budget exhausted.")
                stop_after_tools = True
            elif call["name"] in SOURCE_TOOLS and source_calls >= max_calls:
                rejection = {
                    "ok": False,
                    "error_type": "SourceBudgetExhausted",
                    "error_message": "The source-call budget is exhausted.",
                    "source_calls": 0,
                }
                warnings.append("Source-call budget exhausted.")
                stop_after_tools = True

            if rejection is not None:
                message = ToolMessage(
                    content=json.dumps(rejection),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            else:
                single_message = AIMessage(content="", tool_calls=[call])
                local_state = dict(state)
                local_state["messages"] = [single_message]
                local_state["total_source_calls"] = source_calls
                result = self.tool_node.invoke(local_state)
                returned = {
                    item.tool_call_id: item
                    for item in result.get("messages", [])
                    if isinstance(item, ToolMessage)
                }
                message = returned.get(call["id"])
                if message is None:
                    message = ToolMessage(
                        content=json.dumps(
                            {
                                "ok": False,
                                "error_type": "MissingToolResult",
                                "error_message": "The tool returned no matching result.",
                                "source_calls": 0,
                            }
                        ),
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
            tool_messages.append(message)
            counts[call["name"]] = counts.get(call["name"], 0) + 1
            try:
                payload = json.loads(message.content) if isinstance(message.content, str) else message.content
            except (TypeError, json.JSONDecodeError):
                payload = {"ok": False, "error_type": "InvalidToolResult", "error_message": str(message.content)[:500]}
            source_calls += int(payload.get("source_calls") or 0)
            evidence_ids.extend(payload.get("evidence_ids") or [])
            warnings.extend(payload.get("warnings") or [])
            data = payload.get("data") or {}
            if call["name"] == "search_jobs":
                searched_jobs = True
                page = data.get("page") or data
                new_jobs = list(page.get("items") or [])
                if not new_jobs:
                    new_jobs = list(data.get("verified") or []) + list(
                        data.get("eligibility_not_verified") or []
                    )
                jobs = self._merge_candidates(
                    jobs, new_jobs, state.get("iteration", 0) + 1
                )
                sufficiency = data.get("sufficiency") or {}
                is_sufficient = (
                    int(sufficiency.get("verified_count") or 0)
                    >= int(sufficiency.get("requested_count") or 1)
                )
            elif call["name"] == "search_people":
                searched_people = True
                page = data.get("page") or data
                new_people = list(page.get("items") or data.get("candidates") or [])
                people = self._merge_candidates(
                    people, new_people, state.get("iteration", 0) + 1
                )
                sufficiency = data.get("sufficiency") or {}
                is_sufficient = bool(sufficiency.get("sufficient", False))
            recorder.tool_call(
                tool_call_id=call["id"],
                tool_name=call["name"],
                arguments=call.get("args") or {},
                status="completed" if payload.get("ok") else "failed",
                started=started,
                result_summary=f"ok={payload.get('ok')}; evidence={len(payload.get('evidence_ids') or [])}",
                error_type=payload.get("error_type"),
                error_message=payload.get("error_message"),
            )
        verified_count = sum(bool(item.get("hard_constraints_met")) for item in jobs)
        previous_ids = {
            item.get("candidate_id")
            for item in state.get("job_candidates", [])
            if item.get("hard_constraints_met")
        }
        current_ids = {
            item.get("candidate_id") for item in jobs if item.get("hard_constraints_met")
        }
        consecutive_no_new = state.get("consecutive_no_new_results", 0)
        if searched_jobs or searched_people:
            previous_all = {
                item.get("candidate_id")
                for item in [
                    *state.get("job_candidates", []),
                    *state.get("people_candidates", []),
                ]
            }
            current_all = {
                item.get("candidate_id") for item in [*jobs, *people]
            }
            consecutive_no_new = (
                consecutive_no_new + 1 if not (current_all - previous_all) else 0
            )
        status = AgentStatus(
            goal=state.get("current_goal") or "",
            workflow_stage="reviewing_results",
            completed_steps=["Executed permitted tools"],
            current_step="Summarize results",
            candidate_count=len(jobs) + len(people),
            verified_candidate_count=verified_count,
            unverified_candidate_count=max(0, len(jobs) - verified_count),
            source_call_count=source_calls,
            warnings=warnings,
        )
        if source_calls >= max_calls:
            stop_after_tools = True
        if consecutive_no_new >= int(os.getenv("AGENT_NO_NEW_RESULTS_STOP", "2")):
            stop_after_tools = True
        if is_sufficient:
            stop_after_tools = True
        todos = self._todos_after_execution(state.get("todo_items", []))
        return {
            "messages": tool_messages,
            "tool_call_counts": counts,
            "total_source_calls": source_calls,
            "consecutive_no_new_results": consecutive_no_new,
            "job_candidates": jobs,
            "people_candidates": people,
            "evidence_ids": sorted(set(evidence_ids)),
            "warnings": warnings,
            "status": status.model_dump(mode="json"),
            "todo_items": todos,
            "is_sufficient": is_sufficient,
            "stop_after_tools": stop_after_tools,
            "partial_result": stop_after_tools and not is_sufficient,
            "workflow_stage": "partial" if stop_after_tools and not is_sufficient else "reviewing_results",
        }

    @staticmethod
    def _merge_candidates(
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        iteration: int,
    ) -> list[dict[str, Any]]:
        merged = {item.get("candidate_id"): dict(item) for item in existing}
        order = [item.get("candidate_id") for item in existing]
        for candidate in incoming:
            candidate_id = candidate.get("candidate_id")
            if not candidate_id:
                continue
            previous = merged.get(candidate_id, {})
            combined = {**previous, **candidate}
            combined["first_seen_iteration"] = previous.get(
                "first_seen_iteration", iteration
            )
            combined["last_seen_iteration"] = iteration
            combined["evidence_ids"] = sorted(
                set(previous.get("evidence_ids", []))
                | set(candidate.get("evidence_ids", []))
            )
            combined["source_keys"] = sorted(
                set(previous.get("source_keys", []))
                | set(candidate.get("source_keys", []))
                | ({str(candidate.get("source_name"))} if candidate.get("source_name") else set())
            )
            merged[candidate_id] = combined
            if candidate_id not in order:
                order.append(candidate_id)
        return [merged[item_id] for item_id in order if item_id in merged]

    @staticmethod
    def _todos_after_execution(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = [dict(item) for item in items]
        active_seen = False
        for item in result:
            if item.get("status") == "in_progress" and not active_seen:
                item["status"] = "completed"
                active_seen = True
                continue
            if active_seen and item.get("status") == "pending":
                item["status"] = "in_progress"
                break
        return result

    def _route_after_tools(self, state: CareerAgentState) -> str:
        return "final" if state.get("stop_after_tools") else "continue"

    @staticmethod
    def _route_after_classify(state: CareerAgentState) -> str:
        return "clarify" if state.get("needs_user_input") else "prepare"

    @staticmethod
    def _route_after_prepare(state: CareerAgentState) -> str:
        return (
            "action"
            if (
                CareerIntent(state["intent"]) in ACTION_INTENTS
                or (state.get("status") or {}).get("workflow_stage") == "planning"
            )
            else "respond"
        )

    def _route_after_model(self, state: CareerAgentState) -> str:
        if state.get("current_error"):
            return "final"
        last = state.get("messages", [])[-1] if state.get("messages") else None
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "final"

    def finalize(self, state: CareerAgentState) -> dict[str, Any]:
        final = state.get("final_response") or ""
        if not final and state.get("messages"):
            last = state["messages"][-1]
            if isinstance(last, AIMessage):
                final = str(last.content).strip()
        if not final:
            if state.get("job_candidates"):
                final = f"I found {len(state['job_candidates'])} evidence-backed job candidates. Review the structured results below."
            elif state.get("people_candidates"):
                final = f"I found {len(state['people_candidates'])} evidence-backed people candidates. Review the sources below."
            else:
                final = "I could not complete the workflow. Please provide one more specific target and try again."
        failed = bool(state.get("current_error")) and not state.get("needs_user_input")
        partial = bool(state.get("partial_result"))
        run_status = (
            "failed"
            if failed
            else "needs_input"
            if state.get("needs_user_input")
            else "partial"
            if partial
            else "completed"
        )
        todos = []
        for item in state.get("todo_items", []):
            updated = dict(item)
            if updated.get("status") == "in_progress":
                if state.get("needs_user_input") or failed:
                    updated["status"] = "blocked"
                else:
                    updated["status"] = "completed"
            elif updated.get("status") == "pending":
                if failed or partial:
                    updated["status"] = "cancelled"
                elif not state.get("needs_user_input"):
                    updated["status"] = "completed"
            todos.append(updated)
        self.repository.update_agent_run(
            state["user_id"],
            state["run_id"],
            status=run_status,
            final_summary=final,
            error_summary=state.get("current_error"),
            state_json={
                "workflow_stage": (
                    "failed"
                    if failed
                    else "needs_input"
                    if state.get("needs_user_input")
                    else "partial"
                    if partial
                    else "completed"
                ),
                "todo_items": todos,
                "status": state.get("status", {}),
                "warnings": state.get("warnings", []),
                "candidate_count": len(state.get("job_candidates", []))
                + len(state.get("people_candidates", [])),
                "verified_candidate_count": sum(
                    bool(item.get("hard_constraints_met"))
                    for item in state.get("job_candidates", [])
                ),
                "unverified_candidate_count": sum(
                    not bool(item.get("hard_constraints_met"))
                    for item in state.get("job_candidates", [])
                ),
                "source_call_count": state.get("total_source_calls", 0),
                "personalization_references": state.get("personalization_references", {}),
            },
        )
        TrajectoryRecorder(state["user_id"], state["run_id"], self.repository).step("finalize", "Prepared observable final response.", "failed" if failed else "completed")
        workflow_stage = (
            "failed"
            if failed
            else "needs_input"
            if state.get("needs_user_input")
            else "partial"
            if partial
            else "completed"
        )
        return {
            "final_response": final,
            "todo_items": todos,
            "workflow_stage": workflow_stage,
            "personalization_references": state.get("personalization_references", {}),
        }

    def invoke(self, state: CareerAgentState, config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.graph.invoke(state, config=config)


def build_career_agent_graph(repository: ProfileRepository = profile_repository, context: ContextManager = context_manager, registry: SkillRegistry = skill_registry, checkpointer=None, model_factory: Callable[[str], Any] = get_llm) -> CareerAgentGraph:
    return CareerAgentGraph(repository, context, registry, checkpointer, model_factory)


career_agent_graph = build_career_agent_graph(checkpointer=get_default_checkpointer())
