from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from langgraph.types import interrupt

from app.state.schema import ProfileFacts, ProfileState

REQUIRED_FIELDS = ("school", "major", "graduation_year", "skills", "experience")


def _has_required_value(field: str, value: Any) -> bool:
    if field in {"school", "major"}:
        return isinstance(value, str) and bool(value.strip())
    if field == "graduation_year":
        return value not in (None, "")
    if field == "skills":
        return isinstance(value, list) and any(
            isinstance(item, str) and item.strip() for item in value
        )
    if field == "experience":
        if not isinstance(value, list):
            return False
        return any(
            (
                isinstance(item, dict)
                and any(str(item.get(key) or "").strip() for key in (
                    "organization",
                    "role",
                    "description",
                ))
            )
            or (isinstance(item, str) and item.strip())
            for item in value
        )
    return value is not None


def find_profile_issues(
    profile: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return deterministic missing fields and validation errors."""

    missing_fields: list[str] = []
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if not _has_required_value(field, profile.get(field)):
            missing_fields.append(field)

    graduation_year = profile.get("graduation_year")
    if graduation_year not in (None, ""):
        current_year = datetime.now(timezone.utc).year
        if not isinstance(graduation_year, int):
            errors.append("graduation_year must be a four-digit number")
            if "graduation_year" not in missing_fields:
                missing_fields.append("graduation_year")
        elif graduation_year < 1950 or graduation_year > current_year + 15:
            errors.append(
                f"graduation_year must be between 1950 and {current_year + 15}"
            )
            if "graduation_year" not in missing_fields:
                missing_fields.append("graduation_year")

    return missing_fields, errors


def validate_profile(state: ProfileState) -> dict[str, list[str]]:
    """Validate required profile facts without making an LLM call."""

    missing_fields, errors = find_profile_issues(state["extracted_profile"])
    return {
        "missing_fields": missing_fields,
        "validation_errors": errors,
    }


def merge_profile_updates(
    profile: dict[str, Any], updates: dict[str, Any]
) -> dict[str, Any]:
    """Merge user-supplied corrections and normalize them with Pydantic."""

    merged = deepcopy(profile)
    merged.update(updates)
    return ProfileFacts.model_validate(merged).model_dump()


def collect_missing_information(state: ProfileState) -> dict[str, Any]:
    """Pause the graph so the UI or CLI can provide required missing fields."""

    updates = interrupt(
        {
            "type": "missing_profile_fields",
            "missing_fields": state["missing_fields"],
            "validation_errors": state.get("validation_errors", []),
            "profile": state["extracted_profile"],
        }
    )
    if not isinstance(updates, dict):
        raise TypeError("Missing profile information must be provided as a mapping.")

    merged = merge_profile_updates(state["extracted_profile"], updates)
    return {
        "extracted_profile": merged,
        "profile_updates": updates,
        "confirmed": False,
    }
