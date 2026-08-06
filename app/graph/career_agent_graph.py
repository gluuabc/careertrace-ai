from __future__ import annotations

import json
import os
from typing import Any
from collections.abc import Callable

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.database.repository import ProfileRepository, profile_repository
from app.graph.checkpoint import get_default_checkpointer
from app.llm.model import get_llm
from app.services.context_manager import ContextManager, context_manager
from app.services.skill_registry import SkillRegistry, skill_registry
from app.services.trajectory import TrajectoryRecorder
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


def _fallback_intent(request: str) -> IntentDecision:
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
    elif any(term in value for term in ("plan", "timeline", "steps")):
        intent = CareerIntent.ACTION_PLAN
    else:
        intent = CareerIntent.CONCISE_GUIDANCE
    return IntentDecision(intent=intent, goal=request.strip())


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
        graph.add_edge("classify_intent", "prepare_workflow")
        graph.add_conditional_edges(
            "prepare_workflow",
            lambda state: "action" if state.get("intent") in ACTION_INTENTS else "respond",
            {"action": "plan_action", "respond": "agent_model"},
        )
        graph.add_conditional_edges(
            "plan_action",
            lambda state: "tools" if state.get("messages") and isinstance(state["messages"][-1], AIMessage) and state["messages"][-1].tool_calls else "final",
            {"tools": "execute_tools", "final": "finalize"},
        )
        graph.add_edge("execute_tools", "agent_model")
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
            state["user_id"], state["conversation_id"], goal=request
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
        }

    def classify_intent(self, state: CareerAgentState) -> dict[str, Any]:
        request = state["current_request"]
        try:
            decision = self.model_factory("cheap").with_structured_output(IntentDecision).invoke(
                "Classify this CareerTrace request into exactly one supported intent. "
                "Action requests must use their workflow intent. Ask for clarification only "
                f"when execution is impossible without one fact. Request: {request}"
            )
            if not isinstance(decision, IntentDecision):
                decision = IntentDecision.model_validate(decision)
        except Exception:
            decision = _fallback_intent(request)
        self.repository.update_agent_run(
            state["user_id"],
            state["run_id"],
            intent=decision.intent.value,
            goal=decision.goal,
            status="needs_input" if decision.needs_user_input else "running",
        )
        TrajectoryRecorder(state["user_id"], state["run_id"], self.repository).step(
            "classify_intent", f"Routed request to {decision.intent.value}."
        )
        return {
            "intent": decision.intent,
            "current_goal": decision.goal,
            "needs_user_input": decision.needs_user_input,
            "final_response": decision.clarification_question or "",
            "workflow_stage": "preparing_workflow",
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
        if state.get("needs_user_input"):
            return {}
        intent = CareerIntent(state["intent"])
        tool_name, schema, argument_name = TOOL_BY_INTENT[intent]
        profile = self.repository.get_profile(state["user_id"])
        prompt = (
            "Create only the structured arguments needed for the controlled tool. "
            "Do not invent missing external facts. Use confirmed profile facts when relevant.\n"
            f"REQUEST: {state['current_request']}\nPROFILE: {json.dumps(profile, default=str)}\n"
            f"SKILL: {state.get('loaded_skills', {}).get(state.get('active_skill') or '', '')}"
        )
        try:
            planned = self.model_factory("reasoning").with_structured_output(schema).invoke(prompt)
            if not isinstance(planned, schema):
                planned = schema.model_validate(planned)
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
            return {"messages": [message], "workflow_stage": "executing_tools"}
        except Exception as error:
            response = (
                "I could not safely prepare that workflow because required structured "
                f"information is missing or invalid: {str(error)[:300]}. Please provide the missing target details."
            )
            return {
                "needs_user_input": True,
                "current_error": str(error)[:500],
                "final_response": response,
                "workflow_stage": "needs_input",
            }

    def agent_model(self, state: CareerAgentState) -> dict[str, Any]:
        messages = self.context.build_messages(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            current_request=state["current_request"],
            current_task={"intent": str(state.get("intent")), "goal": state.get("current_goal")},
            selected_entities={"job_ids": state.get("selected_job_ids", []), "people_ids": state.get("selected_people_ids", [])},
            loaded_skills=state.get("loaded_skills", {}),
            agent_status=state.get("status", {}),
        )
        messages.extend(state.get("messages", []))
        base_model = self.model_factory("reasoning")
        model = base_model.bind_tools(CAREER_AGENT_TOOLS)
        try:
            response = model.invoke(messages)
            if not isinstance(response, AIMessage):
                response = base_model.invoke(messages)
        except Exception as error:
            return {"current_error": str(error)[:500], "final_response": "CareerTrace could not complete the model step. The completed tool results remain saved; please retry once.", "workflow_stage": "failed"}
        return {"messages": [response], "iteration": state.get("iteration", 0) + 1, "workflow_stage": "reasoning"}

    def execute_tools(self, state: CareerAgentState) -> dict[str, Any]:
        ai_message = state["messages"][-1]
        if not isinstance(ai_message, AIMessage):
            return {"current_error": "Tool execution requires an assistant tool call."}
        max_calls = int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12"))
        if state.get("total_source_calls", 0) >= max_calls:
            return {"warnings": [*state.get("warnings", []), "Source-call budget exhausted."], "workflow_stage": "budget_exhausted"}
        import time
        started = time.monotonic()
        result = self.tool_node.invoke(state)
        tool_messages = result.get("messages", [])
        recorder = TrajectoryRecorder(state["user_id"], state["run_id"], self.repository)
        counts = dict(state.get("tool_call_counts", {}))
        source_calls = state.get("total_source_calls", 0)
        jobs = list(state.get("job_candidates", []))
        people = list(state.get("people_candidates", []))
        evidence_ids = list(state.get("evidence_ids", []))
        warnings = list(state.get("warnings", []))
        searched_jobs = False
        is_sufficient = bool(state.get("is_sufficient", False))
        for call, message in zip(ai_message.tool_calls, tool_messages, strict=False):
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
                jobs = list(data.get("verified") or []) + list(data.get("eligibility_not_verified") or [])
                sufficiency = data.get("sufficiency") or {}
                is_sufficient = (
                    int(sufficiency.get("verified_count") or 0)
                    >= int(sufficiency.get("requested_count") or 1)
                )
            elif call["name"] == "search_people":
                people = list(data.get("candidates") or [])
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
        if searched_jobs:
            consecutive_no_new = (
                consecutive_no_new + 1 if not (current_ids - previous_ids) else 0
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
        return {"messages": tool_messages, "tool_call_counts": counts, "total_source_calls": min(source_calls, max_calls), "consecutive_no_new_results": consecutive_no_new, "job_candidates": jobs, "people_candidates": people, "evidence_ids": sorted(set(evidence_ids)), "warnings": warnings, "status": status.model_dump(mode="json"), "is_sufficient": is_sufficient, "workflow_stage": "reviewing_results"}

    def _route_after_model(self, state: CareerAgentState) -> str:
        if state.get("current_error"):
            return "final"
        if state.get("is_sufficient"):
            return "final"
        if state.get("total_source_calls", 0) >= int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12")):
            return "final"
        if state.get("consecutive_no_new_results", 0) >= int(
            os.getenv("AGENT_NO_NEW_RESULTS_STOP", "2")
        ):
            return "final"
        last = state.get("messages", [])[-1] if state.get("messages") else None
        if isinstance(last, AIMessage) and last.tool_calls and state.get("iteration", 0) < int(os.getenv("AGENT_MAX_ITERATIONS", "6")):
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
        failed = bool(state.get("current_error"))
        self.repository.update_agent_run(state["user_id"], state["run_id"], status="failed" if failed else ("needs_input" if state.get("needs_user_input") else "completed"), final_summary=final, error_summary=state.get("current_error"))
        todos = []
        for item in state.get("todo_items", []):
            updated = dict(item)
            if updated.get("status") in {"pending", "in_progress"}:
                updated["status"] = "cancelled" if failed else "completed"
            todos.append(updated)
        TrajectoryRecorder(state["user_id"], state["run_id"], self.repository).step("finalize", "Prepared observable final response.", "failed" if failed else "completed")
        return {"final_response": final, "todo_items": todos, "workflow_stage": "failed" if failed else "completed"}

    def invoke(self, state: CareerAgentState, config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.graph.invoke(state, config=config)


def build_career_agent_graph(repository: ProfileRepository = profile_repository, context: ContextManager = context_manager, registry: SkillRegistry = skill_registry, checkpointer=None, model_factory: Callable[[str], Any] = get_llm) -> CareerAgentGraph:
    return CareerAgentGraph(repository, context, registry, checkpointer, model_factory)


career_agent_graph = build_career_agent_graph(checkpointer=get_default_checkpointer())
