from typing import Any

from app.database.repository import ProfileRepository, profile_repository

DEMO_USER_ID = "00000000-0000-4000-8000-000000000001"
DEMO_USER_NAME = "CareerTrace Demo Student"

DEMO_PROFILE: dict[str, Any] = {
    "name": DEMO_USER_NAME,
    "email": None,
    "education": ["B.S. Computer Science, expected 2028"],
    "school": "Northstar Institute of Technology",
    "major": "Computer Science",
    "graduation_year": 2028,
    "career_goal": "Build reliable AI products that improve access to education.",
    "skills": ["Python", "SQL", "Machine Learning", "LangGraph", "AWS"],
    "projects": [
        {
            "title": "Campus Opportunity Navigator",
            "description": (
                "Built a synthetic-data career discovery prototype with semantic "
                "search and explainable recommendations."
            ),
        },
        {
            "title": "Study Group Matcher",
            "description": (
                "Created a privacy-conscious matching service for student study "
                "preferences."
            ),
        },
    ],
    "experience": [
        {
            "organization": "Northstar AI Lab",
            "role": "Student Research Assistant",
            "description": (
                "Evaluated retrieval pipelines and documented model-quality tests."
            ),
        },
        {
            "organization": "Open Source Student Club",
            "role": "Project Lead",
            "description": "Led four students building a Python mentoring tool.",
        },
    ],
    "target_roles": ["Machine Learning Engineer Intern", "AI Product Engineer"],
    "preferred_locations": ["United States", "Remote"],
    "employment_types": ["Internship"],
    "work_authorization": "Synthetic demo value",
    "remote_preference": "Flexible",
}

DEMO_ANALYSIS: dict[str, list[str]] = {
    "strengths": [
        "Hands-on Python and SQL experience",
        "Applied AI project leadership",
        "Experience evaluating retrieval systems",
    ],
    "possible_roles": [
        "Machine Learning Engineer Intern",
        "AI Product Engineer Intern",
        "Data and Applied AI Intern",
    ],
    "recommended_next_skills": [
        "Production MLOps and monitoring",
        "Evaluation design for RAG systems",
        "Cloud deployment and infrastructure as code",
    ],
}


def reset_demo_data(
    repository: ProfileRepository = profile_repository,
) -> dict[str, Any]:
    """Restore the fixed judge workspace to its deterministic synthetic seed."""

    return repository.reset_demo_account(
        demo_user_id=DEMO_USER_ID,
        name=DEMO_USER_NAME,
        profile_data=DEMO_PROFILE,
        analysis_data=DEMO_ANALYSIS,
    )


def get_or_seed_demo_user(
    repository: ProfileRepository = profile_repository,
) -> dict[str, Any]:
    """Load the demo identity, seeding it lazily when first requested."""

    try:
        return repository.get_demo_user(DEMO_USER_ID)
    except ValueError:
        return reset_demo_data(repository)
