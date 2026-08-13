"""Stable Career Agent tool registry with lazy imports.

Keeping package initialization import-free prevents source adapters from creating
cycles when they are imported independently by services or tests.
"""

from __future__ import annotations

from typing import Any


def _build_tools() -> list[Any]:
    from app.tools.drafts import (
        save_outreach_draft,
        save_resume_revision_draft,
        update_outreach_status,
    )
    from app.tools.evidence import read_evidence
    from app.tools.jobs import get_job_details, search_jobs
    from app.tools.people import get_person_details, search_people
    from app.tools.skills import read_skill_file

    return [
        read_skill_file,
        read_evidence,
        search_jobs,
        get_job_details,
        search_people,
        get_person_details,
        save_resume_revision_draft,
        save_outreach_draft,
        update_outreach_status,
    ]


def __getattr__(name: str) -> Any:
    if name == "CAREER_AGENT_TOOLS":
        value = _build_tools()
        globals()[name] = value
        return value
    raise AttributeError(name)


__all__ = ["CAREER_AGENT_TOOLS"]
