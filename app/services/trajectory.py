from __future__ import annotations

import re
from time import monotonic
from typing import Any

from app.database.repository import ProfileRepository, profile_repository

SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|secret|password|token|cookie|authorization|credential)", re.I
)
PRIVATE_REASONING_KEY = re.compile(r"(chain[_-]?of[_-]?thought|hidden_reasoning|thinking)", re.I)


def sanitize_arguments(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)):
                result[str(key)] = "[REDACTED]"
            elif PRIVATE_REASONING_KEY.search(str(key)):
                continue
            else:
                result[str(key)] = sanitize_arguments(item)
        return result
    if isinstance(value, list):
        return [sanitize_arguments(item) for item in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…[truncated]"
    return value


class TrajectoryRecorder:
    def __init__(
        self,
        user_id: str,
        run_id: str,
        repository: ProfileRepository = profile_repository,
    ):
        self.user_id = user_id
        self.run_id = run_id
        self.repository = repository

    def step(self, stage: str, summary: str, status: str = "completed") -> dict[str, Any]:
        return self.repository.create_agent_step(
            self.user_id,
            self.run_id,
            stage=stage,
            status=status,
            display_summary=summary,
        )

    def tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        started: float,
        result_summary: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        step_id: str | None = None,
    ) -> dict[str, Any]:
        return self.repository.record_agent_tool_call(
            self.user_id,
            self.run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            sanitized_arguments=sanitize_arguments(arguments),
            status=status,
            result_summary=(result_summary or "")[:2000] or None,
            error_type=error_type,
            error_message=(error_message or "")[:1000] or None,
            duration_ms=max(0, round((monotonic() - started) * 1000)),
            step_id=step_id,
        )
