from __future__ import annotations

import pytest

from app.graph.career_agent_graph import enforce_intent_boundaries
from app.services.job_search import apply_hard_filters
from app.services.memory_signals import detect_memory_signals, merge_memory_signals
from app.state.agent_schema import (
    CareerIntent,
    IntentDecision,
    JobCandidate,
    JobSearchRequest,
    RequirementState,
)


@pytest.mark.parametrize(
    ("prompt", "raw", "expected"),
    [
        ("Find 5 current ML internships in Los Angeles.", CareerIntent.JOB_SEARCH, CareerIntent.JOB_SEARCH),
        ("Which role fits me better: ML Engineer or Data Scientist?", CareerIntent.JOB_SEARCH, CareerIntent.CONCISE_GUIDANCE),
        ("Find alumni from Example University working in robotics.", CareerIntent.PEOPLE_SEARCH, CareerIntent.PEOPLE_SEARCH),
        ("Tailor my resume summary for backend engineering.", CareerIntent.RESUME_REVISION, CareerIntent.RESUME_REVISION),
        ("Draft a message to this recruiter.", CareerIntent.OUTREACH, CareerIntent.OUTREACH),
        ("What skills am I missing for ML engineering?", CareerIntent.JOB_SEARCH, CareerIntent.CONCISE_GUIDANCE),
        ("Help me with my career.", CareerIntent.CONCISE_GUIDANCE, CareerIntent.CLARIFICATION),
        ("Which of those should I apply to first?", CareerIntent.JOB_SEARCH, CareerIntent.CONCISE_GUIDANCE),
        ("Now help me rewrite my resume summary.", CareerIntent.RESUME_REVISION, CareerIntent.RESUME_REVISION),
        ("Find internships and draft outreach to recruiters.", CareerIntent.JOB_SEARCH, CareerIntent.CLARIFICATION),
    ],
)
def test_routing_regression_corpus(prompt, raw, expected):
    bounded, _changed = enforce_intent_boundaries(
        prompt, IntentDecision(intent=raw, goal=prompt)
    )
    assert bounded.intent == expected


@pytest.mark.parametrize(
    ("prompt", "expected_types"),
    [
        ("My major is computer science.", ["profile.major"]),
        ("I prefer remote roles.", ["memory.preference"]),
        ("My career goal is ML engineering.", ["memory.goal"]),
        ("I cannot relocate outside California.", ["memory.constraint"]),
        ("I recently accepted a research assistant position.", ["memory.event"]),
        ("Which role fits me better, ML Engineer or Data Scientist?", []),
        ("If I became an ML engineer, would Python be enough?", []),
        ("What kinds of internships should I explore?", []),
        ("You suggested data science earlier. Is that right for me?", []),
        ("I no longer prefer remote roles.", ["memory.preference"]),
        ("My goal is ML engineering. Which role fits me now?", ["memory.goal"]),
    ],
)
def test_memory_regression_corpus(prompt, expected_types):
    explicit = detect_memory_signals(prompt)
    unsupported_classifier_proposal = detect_memory_signals(
        "My career goal is an unsupported classifier value."
    )
    validated = merge_memory_signals(unsupported_classifier_proposal, explicit)
    assert [item.type for item in validated] == expected_types


def _candidate(**updates) -> JobCandidate:
    values = {
        "candidate_id": "audit-job",
        "title": "Software Engineer Intern",
        "company": "Example",
        "location": "New York",
        "employment_type": "Internship",
        "eligibility": "Undergraduate students graduating in 2028; must be authorized to work in the United States without sponsorship.",
        "source_name": "synthetic",
        "source_url": "https://example.com/job",
    }
    values.update(updates)
    return JobCandidate(**values)


def test_requirement_explicit_match():
    result = apply_hard_filters(
        _candidate(),
        JobSearchRequest(
            locations=["New York"], employment_types=["Internship"],
            student_level="undergraduate", graduation_year=2028,
            work_authorization_requirement="authorized to work",
        ),
    )
    assert result.hard_constraints_met is True
    assert all(value == RequirementState.MATCH for value in result.hard_requirement_states.values())


def test_requirement_explicit_conflict_and_conflict_miss_rate_case():
    result = apply_hard_filters(
        _candidate(
            title="Senior Software Engineer",
            location="London",
            employment_type="Full-time",
            eligibility="Graduate students graduating in 2027; sponsorship required.",
        ),
        JobSearchRequest(
            target_roles=["Software Engineer Intern"], locations=["New York"],
            employment_types=["Internship"], student_level="undergraduate",
            graduation_year=2028, work_authorization_requirement="authorized to work",
        ),
    )
    assert {"location", "employment_type", "seniority", "student_level", "graduation_year", "work_authorization"} <= {
        key for key, value in result.hard_requirement_states.items() if value == RequirementState.CONFLICT
    }


@pytest.mark.parametrize(
    ("updates", "job_request", "field"),
    [
        ({"eligibility": None}, JobSearchRequest(student_level="undergraduate"), "student_level"),
        ({"eligibility": "Sponsorship may be considered case-by-case."}, JobSearchRequest(work_authorization_requirement="require sponsorship"), "work_authorization"),
        ({"location": "United States — multiple locations"}, JobSearchRequest(locations=["New York"]), "location"),
    ],
)
def test_ambiguous_or_missing_requirement_remains_unknown(updates, job_request, field):
    result = apply_hard_filters(_candidate(**updates), job_request)
    assert result.hard_requirement_states[field] == RequirementState.UNKNOWN
    assert result.hard_constraints_met is False


def test_internship_vs_full_time_is_conflict():
    result = apply_hard_filters(
        _candidate(employment_type="Full-time"),
        JobSearchRequest(employment_types=["Internship"]),
    )
    assert result.hard_requirement_states["employment_type"] == RequirementState.CONFLICT


def test_skill_preference_is_not_promoted_to_hard_requirement():
    result = apply_hard_filters(
        _candidate(description_excerpt="Java role"),
        JobSearchRequest(desired_job_skills=["Python"]),
    )
    assert "desired_job_skills" not in result.hard_requirement_states
    assert result.hard_constraints_met is True
