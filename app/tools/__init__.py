from app.tools.drafts import save_outreach_draft, save_resume_revision_draft, update_outreach_status
from app.tools.jobs import get_job_details, search_jobs
from app.tools.people import get_person_details, search_people
from app.tools.skills import read_skill, read_skill_file

CAREER_AGENT_TOOLS = [
    read_skill,
    read_skill_file,
    search_jobs,
    get_job_details,
    search_people,
    get_person_details,
    save_resume_revision_draft,
    save_outreach_draft,
    update_outreach_status,
]

__all__ = ["CAREER_AGENT_TOOLS"]
