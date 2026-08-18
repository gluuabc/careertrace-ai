"""User-scoped data assembly and deterministic dashboard recommendations."""

from dataclasses import dataclass
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.nodes.validation import find_profile_issues


@dataclass(frozen=True)
class DashboardSnapshot:
    user: dict[str, Any]
    profile: dict[str, Any] | None
    semantic_memories: list[dict[str, Any]]
    career_events: list[dict[str, Any]]
    pending_memory_candidates: list[dict[str, Any]]
    pending_profile_drafts: list[dict[str, Any]]
    conversations: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    analysis: dict[str, Any] | None
    profile_completion: int
    insight: str
    recommendation: str
    recommendation_metadata: str

    @property
    def pending_review_count(self) -> int:
        return len(self.pending_memory_candidates) + len(self.pending_profile_drafts)


def _profile_completion(profile: dict[str, Any] | None) -> int:
    if not profile:
        return 0
    required = ("school", "major", "graduation_year", "skills", "experience")
    completed = 0
    for field in required:
        value = profile.get(field)
        if isinstance(value, str):
            completed += bool(value.strip())
        else:
            completed += bool(value)
    return round(completed / len(required) * 100)


def _first_memory(memories: list[dict[str, Any]], *groups: str) -> str | None:
    allowed = {group.casefold() for group in groups}
    for item in memories:
        if str(item.get("semantic_group") or "").casefold() in allowed:
            value = str(item.get("value") or "").strip()
            if value:
                return value
    return None


def _insight(
    profile: dict[str, Any] | None,
    memories: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
) -> str:
    roles = list((analysis or {}).get("possible_roles") or [])
    strengths = list((analysis or {}).get("strengths") or [])
    if roles and strengths:
        return f"Your saved analysis connects {strengths[0]} with opportunities such as {roles[0]}."
    goal = _first_memory(memories, "goal", "goals")
    if goal:
        return f"Your approved long-term context is currently oriented around: {goal}"
    skills = list((profile or {}).get("skills") or [])
    if skills:
        return f"Your career identity is currently anchored by {len(skills)} documented skill{'s' if len(skills) != 1 else ''}, led by {skills[0]}."
    return "Add career evidence and approved context so CareerTrace can build a clearer picture of your direction."


def _recommendation(
    profile: dict[str, Any] | None,
    memories: list[dict[str, Any]],
    pending_count: int,
    documents: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
) -> tuple[str, str]:
    if pending_count:
        return (
            f"Review {pending_count} pending career-memory suggestion{'s' if pending_count != 1 else ''}.",
            "Open Memory Universe",
        )
    if not documents:
        return "Upload a resume or career document to establish your evidence base.", "Open Documents"
    if not profile:
        return "Complete document extraction and confirm your career profile.", "Open My profile"
    missing, _ = find_profile_issues(profile)
    if missing:
        readable = ", ".join(field.replace("_", " ") for field in missing[:2])
        return f"Complete your profile by adding {readable}.", "Open My profile"
    next_skills = list((analysis or {}).get("recommended_next_skills") or [])
    if next_skills:
        return f"Consider strengthening {next_skills[0]} as your next development focus.", "From saved analysis"
    if not memories:
        return "Tell Career Assistant about a durable goal or preference for more personal guidance.", "Open AI conversations"
    return "Continue your career conversation to keep your identity current.", "Open AI conversations"


def load_dashboard_snapshot(
    user_id: str,
    *,
    repository: ProfileRepository = profile_repository,
) -> DashboardSnapshot:
    user = repository.get_user(user_id)
    profile = repository.get_profile(user_id)
    semantic_memories = repository.list_semantic_memories(user_id)
    career_events = repository.list_career_events(user_id)
    candidates = [
        item
        for item in repository.list_memory_candidates(user_id)
        if item.get("status") == "pending"
    ]
    drafts = [
        item
        for item in repository.list_profile_revision_drafts(user_id)
        if item.get("status") == "pending"
    ]
    conversations = repository.list_conversations(user_id)
    documents = repository.list_documents(user_id)
    analysis = repository.get_latest_analysis(user_id)
    pending_count = len(candidates) + len(drafts)
    recommendation, recommendation_metadata = _recommendation(
        profile, semantic_memories, pending_count, documents, analysis
    )
    return DashboardSnapshot(
        user=user,
        profile=profile,
        semantic_memories=semantic_memories,
        career_events=career_events,
        pending_memory_candidates=candidates,
        pending_profile_drafts=drafts,
        conversations=conversations,
        documents=documents,
        analysis=analysis,
        profile_completion=_profile_completion(profile),
        insight=_insight(profile, semantic_memories, analysis),
        recommendation=recommendation,
        recommendation_metadata=recommendation_metadata,
    )
