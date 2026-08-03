from typing import Any

from langgraph.types import interrupt

from app.nodes.validation import find_profile_issues, merge_profile_updates
from app.state.schema import ProfileState


def process_confirmation_response(
    current_profile: dict[str, Any], response: bool | dict[str, Any]
) -> dict[str, Any]:
    """Normalize one confirmation response and validate its current values."""

    if isinstance(response, bool):
        requested_confirmation = response
        submitted_profile = current_profile
    elif isinstance(response, dict):
        requested_confirmation = bool(response.get("confirmed"))
        submitted_profile = response.get("profile", current_profile)
        if not isinstance(submitted_profile, dict):
            raise TypeError("Confirmed profile must be provided as a mapping.")
    else:
        raise TypeError("Confirmation must be a boolean or a response mapping.")

    profile = merge_profile_updates(current_profile, submitted_profile)
    missing_fields, errors = find_profile_issues(profile)
    return {
        "confirmation_attempted": requested_confirmation,
        "confirmed": requested_confirmation and not missing_fields and not errors,
        "extracted_profile": profile,
        "missing_fields": missing_fields,
        "validation_errors": errors,
    }


def confirm_profile(state: ProfileState) -> dict[str, Any]:
    """Pause for a final editable human approval before persistence."""

    current_profile = merge_profile_updates(state["extracted_profile"], {})
    missing_fields, errors = find_profile_issues(current_profile)
    response = interrupt(
        {
            "type": "confirm_profile",
            "profile": current_profile,
            "missing_fields": missing_fields,
            "validation_errors": errors,
        }
    )
    return process_confirmation_response(current_profile, response)
