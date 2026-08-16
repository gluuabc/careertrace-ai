from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from app.database.repository import ProfileRepository, profile_repository


RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class JudgeAccessError(ValueError):
    """Raised without distinguishing disabled, invalid, or unknown credentials."""


def judge_demo_enabled() -> bool:
    return os.getenv("JUDGE_DEMO_ENABLED", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }


def validate_judge_access_code(candidate: str) -> None:
    configured = os.getenv("JUDGE_DEMO_ACCESS_CODE", "")
    valid = bool(configured) and hmac.compare_digest(
        candidate.strip().encode("utf-8"), configured.encode("utf-8")
    )
    if not judge_demo_enabled() or not valid:
        raise JudgeAccessError("Judge Demo access is unavailable or the access code is invalid.")


def normalize_recovery_code(value: str) -> str:
    compact = "".join(character for character in value.upper() if character.isalnum())
    if compact.startswith("CT"):
        compact = compact[2:]
    if len(compact) != 16 or any(character not in RECOVERY_ALPHABET for character in compact):
        raise JudgeAccessError("Judge workspace recovery failed.")
    return "CT-" + "-".join(compact[index:index + 4] for index in range(0, 16, 4))


def hash_recovery_code(value: str) -> str:
    normalized = normalize_recovery_code(value)
    return hashlib.sha256(f"careertrace-judge-recovery:{normalized}".encode()).hexdigest()


def generate_recovery_code() -> str:
    characters = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(16))
    return "CT-" + "-".join(characters[index:index + 4] for index in range(0, 16, 4))


def start_judge_workspace(
    access_code: str,
    repository: ProfileRepository = profile_repository,
) -> tuple[dict, str]:
    validate_judge_access_code(access_code)
    # A digest collision is cryptographically implausible; bounded retry keeps the
    # persistence boundary deterministic if a mocked generator repeats in tests.
    for _ in range(3):
        recovery_code = generate_recovery_code()
        try:
            user = repository.create_judge_workspace(hash_recovery_code(recovery_code))
            return user, recovery_code
        except Exception as error:
            if "unique" not in str(error).casefold():
                raise
    raise RuntimeError("Could not allocate a unique judge workspace credential.")


def resume_judge_workspace(
    access_code: str,
    recovery_code: str,
    repository: ProfileRepository = profile_repository,
) -> dict:
    validate_judge_access_code(access_code)
    try:
        digest = hash_recovery_code(recovery_code)
        user = repository.get_judge_workspace_by_recovery_hash(digest)
    except (JudgeAccessError, ValueError) as error:
        raise JudgeAccessError("Judge workspace recovery failed.") from error
    if user.get("is_demo") is not True or user.get("google_id"):
        raise JudgeAccessError("Judge workspace recovery failed.")
    return user
