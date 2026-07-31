from typing import Any

from langgraph.types import interrupt

from app.nodes.validation import find_profile_issues, merge_profile_updates
from app.state.schema import ProfileState


def confirm_profile(state: ProfileState) -> dict[str, Any]:
    """Pause for a final editable human approval before persistence."""

    response = interrupt(
        {
            "type": "confirm_profile",
            "profile": state["extracted_profile"],
        }
    )
    if isinstance(response, bool):
        confirmed = response
        profile = state["extracted_profile"]
    elif isinstance(response, dict):
        confirmed = bool(response.get("confirmed"))
        submitted_profile = response.get("profile", state["extracted_profile"])
        if not isinstance(submitted_profile, dict):
            raise TypeError("Confirmed profile must be provided as a mapping.")
        profile = merge_profile_updates(
            state["extracted_profile"], submitted_profile
        )
    else:
        raise TypeError("Confirmation must be a boolean or a response mapping.")

    if confirmed:
        missing_fields, errors = find_profile_issues(profile)
        if missing_fields or errors:
            details = ", ".join(missing_fields + errors)
            raise ValueError(f"Cannot confirm an incomplete profile: {details}")

    return {
        "confirmed": confirmed,
        "extracted_profile": profile,
    }
