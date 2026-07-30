import json
from pathlib import Path

from app.state.schema import ProfileState


PROFILE_MEMORY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "profile_memory.json"
)


def save_profile(state: ProfileState) -> dict[str, dict]:
    """Deterministically persist confirmed facts to temporary JSON memory."""

    if not state.get("confirmed"):
        raise ValueError("Profile data must be confirmed before it can be saved.")

    profile = state["extracted_profile"]
    PROFILE_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Replace the file atomically so an interrupted write does not corrupt memory.
    temporary_path = PROFILE_MEMORY_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(PROFILE_MEMORY_PATH)

    return {"saved_profile": profile}
