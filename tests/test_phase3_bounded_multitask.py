from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.database.database import (
    create_database_engine,
    create_session_factory,
    init_db,
)
from app.database.repository import ProfileRepository
from app.graph.career_agent_graph import (
    CareerAgentGraph,
    build_validated_task_plan,
)
from app.services.context_manager import ContextManager
from app.services.skill_registry import SkillRegistry
from app.state.agent_schema import (
    CareerIntent,
    IntentDecision,
    JobSearchRequest,
    PeopleSearchRequest,
    TaskPlanItem,
    TaskType,
)


class ScriptedModel:
    def __init__(self, factory: "ScriptedFactory", schema=None):
        self.factory = factory
        self.schema = schema

    def with_structured_output(self, schema):
        return ScriptedModel(self.factory, schema)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        if self.schema is IntentDecision:
            return self.factory.decision
        if self.schema is PeopleSearchRequest:
            self.factory.events.append("plan_people")
            return PeopleSearchRequest(person_type="alumni")
        if self.schema is JobSearchRequest:
            self.factory.events.append("plan_jobs")
            return JobSearchRequest(
                target_roles=["Machine learning"],
                locations=["California"],
                employment_types=["Internship"],
            )
        self.factory.events.append("guidance")
        self.factory.guidance_calls += 1
        return AIMessage(content=self.factory.guidance_text)


class ScriptedFactory:
    def __init__(self, decision: IntentDecision, guidance_text: str):
        self.decision = decision
        self.guidance_text = guidance_text
        self.guidance_calls = 0
        self.events: list[str] = []

    def __call__(self, _model_type: str):
        return ScriptedModel(self)


class ControlledToolNode:
    def __init__(self, events: list[str]):
        self.events = events

    def invoke(self, state):
        call = state["messages"][-1].tool_calls[0]
        self.events.append(call["name"])
        if call["name"] == "search_people":
            payload = {
                "ok": True,
                "data": {
                    "page": {
                        "items": [
                            {
                                "candidate_id": "person-1",
                                "name": "Synthetic Alum",
                                "source_url": "https://example.edu/alumni/1",
                                "evidence_ids": ["evidence-person-1"],
                            }
                        ]
                    },
                    "sufficiency": {
                        "sufficient": True,
                        "requested_count": 1,
                        "verified_count": 1,
                    },
                },
                "source_calls": 1,
                "evidence_ids": ["evidence-person-1"],
            }
        elif call["name"] == "search_jobs":
            payload = {
                "ok": True,
                "data": {
                    "page": {
                        "items": [
                            {
                                "candidate_id": "job-1",
                                "title": "ML Intern",
                                "company": "Synthetic Labs",
                                "source_url": "https://example.com/jobs/1",
                                "hard_constraints_met": True,
                                "evidence_ids": ["evidence-job-1"],
                            }
                        ]
                    },
                    "sufficiency": {
                        "requested_count": 1,
                        "verified_count": 1,
                    },
                },
                "source_calls": 1,
                "evidence_ids": ["evidence-job-1"],
            }
        else:
            raise AssertionError(f"Unexpected tool: {call['name']}")
        return {
            "messages": [
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            ]
        }


@pytest.fixture
def graph_workspace():
    engine = create_database_engine("sqlite://")
    init_db(engine)
    repository = ProfileRepository(create_session_factory(engine))
    user = repository.get_or_create_user("Multi Task Student", "multi@example.com")
    repository.upsert_profile(
        user["user_id"],
        {
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": 2028,
            "skills": ["Python"],
            "experience": [{"role": "Student researcher"}],
        },
    )
    conversation = repository.create_conversation(user["user_id"], "Multi task")
    yield repository, user, conversation
    engine.dispose()


def run_graph(graph_workspace, request: str, decision: IntentDecision, guidance: str):
    repository, user, conversation = graph_workspace
    registry = SkillRegistry()
    factory = ScriptedFactory(decision, guidance)
    graph = CareerAgentGraph(
        repository,
        ContextManager(repository, registry),
        registry,
        model_factory=factory,
    )
    graph.tool_node = ControlledToolNode(factory.events)
    result = graph.invoke(
        {
            "user_id": user["user_id"],
            "conversation_id": conversation["conversation_id"],
            "current_request": request,
            "messages": [],
        }
    )
    return result, factory


def test_guidance_and_people_search_both_complete_before_final(graph_workspace):
    request = (
        "If I wanted to work in product management one day, what should I do? "
        "Also, find me some alumni I can connect to."
    )
    decision = IntentDecision(
        intent=CareerIntent.PEOPLE_SEARCH,
        goal=request,
        task_plan=[
            TaskPlanItem(task_id="g", task_type=TaskType.GUIDANCE, goal="PM preparation"),
            TaskPlanItem(task_id="p", task_type=TaskType.PEOPLE_SEARCH, goal="Find alumni"),
        ],
    )

    result, factory = run_graph(
        graph_workspace,
        request,
        decision,
        "Build cross-functional product experience and practice customer discovery.",
    )

    assert factory.events.index("search_people") < factory.events.index("guidance")
    assert factory.guidance_calls == 1
    assert len(result["people_candidates"]) == 1
    assert {item["task_type"]: item["status"] for item in result["task_plan"]} == {
        "guidance": "completed",
        "people_search": "completed",
    }
    assert "product experience" in result["final_response"]
    assert "1 evidence-backed people match" in result["final_response"]


def test_job_search_and_guidance_both_complete(graph_workspace):
    request = "Find me ML internships in California and tell me how to improve my chances."
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=request)

    result, factory = run_graph(
        graph_workspace,
        request,
        decision,
        "Strengthen one deployed ML project and quantify its outcome.",
    )

    assert factory.events.index("search_jobs") < factory.events.index("guidance")
    assert len(result["job_candidates"]) == 1
    assert [item["task_type"] for item in result["task_plan"]] == [
        "guidance",
        "job_search",
    ]
    assert all(item["status"] == "completed" for item in result["task_plan"])
    assert "deployed ML project" in result["final_response"]
    assert "structured details and sources" in result["final_response"]


def test_people_and_job_search_requires_clarification(graph_workspace):
    request = "Find alumni and find jobs."
    decision = IntentDecision(intent=CareerIntent.JOB_SEARCH, goal=request)

    result, factory = run_graph(graph_workspace, request, decision, "unused")

    assert result["needs_user_input"] is True
    assert result["task_plan"] == []
    assert "people or jobs" in result["final_response"]
    assert factory.events == []


@pytest.mark.parametrize(
    ("prompt", "decision", "expected"),
    [
        (
            "What should I prioritize for a PM career?",
            IntentDecision(intent=CareerIntent.CONCISE_GUIDANCE, goal="PM advice"),
            [TaskType.GUIDANCE],
        ),
        (
            "Find alumni from my university.",
            IntentDecision(intent=CareerIntent.PEOPLE_SEARCH, goal="Find alumni"),
            [TaskType.PEOPLE_SEARCH],
        ),
        (
            "Find ML internships.",
            IntentDecision(intent=CareerIntent.JOB_SEARCH, goal="Find internships"),
            [TaskType.JOB_SEARCH],
        ),
    ],
)
def test_existing_single_task_requests_keep_one_task(prompt, decision, expected):
    plan, actions = build_validated_task_plan(prompt, decision)
    assert [item.task_type for item in plan] == expected
    assert len(actions) <= 1


def test_deterministic_validation_recovers_missed_action_and_drops_invented_action():
    recovered, _actions = build_validated_task_plan(
        "Please find me alumni I can contact.",
        IntentDecision(
            intent=CareerIntent.CONCISE_GUIDANCE,
            goal="General advice",
            task_plan=[
                TaskPlanItem(
                    task_type=TaskType.JOB_SEARCH,
                    goal="Invented job search",
                )
            ],
        ),
    )
    assert [item.task_type for item in recovered] == [TaskType.PEOPLE_SEARCH]


def test_public_response_excludes_internal_orchestration_terms(graph_workspace):
    request = "Find alumni and tell me how to prepare for outreach."
    decision = IntentDecision(intent=CareerIntent.PEOPLE_SEARCH, goal=request)
    result, _factory = run_graph(
        graph_workspace,
        request,
        decision,
        "The workflow node used routing intent and tool execution internally.",
    )
    lowered = result["final_response"].casefold()
    for forbidden in ("workflow", "node", "routing", "intent", "tool execution"):
        assert forbidden not in lowered
