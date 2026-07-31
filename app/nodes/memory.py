from typing import Any

from app.database import init_db, profile_repository
from app.state.schema import ProfileState

def save_profile(state: ProfileState) -> dict[str, dict]:
    """Deterministically persist one confirmed profile transactionally."""

    if not state.get("confirmed"):
        raise ValueError("Profile data must be confirmed before it can be saved.")

    init_db()
    profile = state["extracted_profile"]
    user_id = state.get("user_id")
    if not user_id:
        user = profile_repository.get_or_create_user(
            name=profile.get("name") or "CareerTrace User",
            email=profile.get("email"),
        )
        user_id = user["user_id"]

    saved_profile = profile_repository.upsert_profile(user_id, profile)
    return {
        "user_id": user_id,
        "saved_profile": saved_profile,
    }


def save_career_analysis(state: ProfileState) -> dict[str, Any]:
    """Persist generated analysis separately so analysis history is retained."""

    analysis = profile_repository.save_analysis(
        state["user_id"], state["career_profile"]
    )
    return {"analysis_id": analysis["analysis_id"]}
