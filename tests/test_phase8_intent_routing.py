from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.graph.career_agent_graph import (
    CareerAgentGraph,
    _fallback_intent,
    enforce_intent_boundaries,
)
from app.services.context_manager import ContextManager
from app.services.skill_registry import SkillRegistry
from app.state.agent_schema import CareerIntent, IntentDecision


class StaticClassifier:
    def __init__(self, decision: IntentDecision):
        self.decision = decision

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return self.decision


class FailingClassifier:
    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        raise RuntimeError("synthetic classifier failure")


@pytest.fixture
def routing_context():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Routing Student", "routing@example.com")
    repository.upsert_profile(
        user["user_id"],
        {
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "skills": ["Python"],
            "experience": [{"role": "Software Intern"}],
        },
    )
    conversation = repository.create_conversation(user["user_id"], "Routing")
    yield repository, user, conversation
    engine.dispose()


def classify(routing_context, request: str, decision: IntentDecision):
    repository, user, conversation = routing_context
    registry = SkillRegistry()
    graph = CareerAgentGraph(
        repository,
        ContextManager(repository, registry),
        registry,
        model_factory=lambda _kind: StaticClassifier(decision),
    )
    run = repository.create_agent_run(
        user["user_id"], conversation["conversation_id"], goal=request
    )
    result = graph.classify_intent(
        {
            "user_id": user["user_id"],
            "conversation_id": conversation["conversation_id"],
            "run_id": run["run_id"],
            "current_request": request,
            "warnings": [],
        }
    )
    return graph, run, result


@pytest.mark.parametrize(
    "prompt",
    [
        "Which role fits me better: ML Engineer, Software Engineer, or Data Scientist?",
        "Which internship role is the best fit for my background?",
        "What are the main requirements for software engineering internships?",
    ],
)
def test_llm_job_search_is_overridden_for_role_guidance(routing_context, prompt):
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=prompt)

    _graph, _run, result = classify(routing_context, prompt, decision)

    assert result["intent"] == CareerIntent.CONCISE_GUIDANCE
    assert result["routing_source"] == "llm_boundary_override"


@pytest.mark.parametrize(
    "prompt",
    [
        "Find me ML internships.",
        "Which role fits me best, then find 5 current openings for that role.",
    ],
)
def test_explicit_and_mixed_retrieval_remain_job_search(routing_context, prompt):
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=prompt)

    _graph, _run, result = classify(routing_context, prompt, decision)

    assert result["intent"] == CareerIntent.JOB_SEARCH
    assert result["routing_source"] == "llm"


def test_structured_job_search_retains_count_and_location(routing_context):
    request = "Find 5 ML internships in Los Angeles."
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=request)
    graph, run, classified = classify(routing_context, request, decision)
    repository, user, conversation = routing_context

    planned = graph.plan_action(
        {
            "user_id": user["user_id"],
            "conversation_id": conversation["conversation_id"],
            "run_id": run["run_id"],
            "current_request": request,
            "intent": classified["intent"],
            "iteration": 0,
            "loaded_skills": {},
        }
    )

    tool_call = planned["messages"][0].tool_calls[0]
    assert tool_call["name"] == "search_jobs"
    assert tool_call["args"]["request"]["requested_count"] == 5
    assert tool_call["args"]["request"]["max_results"] == 10
    assert tool_call["args"]["request"]["locations"] == ["Los Angeles"]


def test_fallback_distinguishes_guidance_from_retrieval():
    assert (
        _fallback_intent("Which internship role fits me?").intent
        == CareerIntent.CONCISE_GUIDANCE
    )
    assert (
        _fallback_intent("Find internships for me.").intent
        == CareerIntent.JOB_SEARCH
    )


def test_guidance_route_cannot_enter_job_tool_workflow(routing_context):
    request = "Which role fits me better: ML Engineer or Software Engineer?"
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=request)
    graph, run, classified = classify(routing_context, request, decision)

    prepared = graph.prepare_workflow(
        {
            "user_id": routing_context[1]["user_id"],
            "run_id": run["run_id"],
            "current_request": request,
            "current_goal": request,
            "intent": classified["intent"],
        }
    )

    assert graph._route_after_prepare({"intent": classified["intent"]}) == "respond"
    assert prepared["active_skill"] is None
    assert prepared["status"]["source_call_count"] == 0


@pytest.mark.parametrize(
    "intent",
    [CareerIntent.PEOPLE_SEARCH, CareerIntent.RESUME_REVISION, CareerIntent.OUTREACH],
)
def test_boundary_does_not_hijack_unrelated_intents(intent):
    decision = IntentDecision(intent=intent, goal="Unrelated workflow")

    bounded, changed = enforce_intent_boundaries("Find a relevant person", decision)

    assert bounded.intent == intent
    assert changed is False
