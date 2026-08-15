from __future__ import annotations

import re
from typing import Any

from app.state.agent_schema import MemorySignal


def _values(value: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", value).strip(" ,.;:!?")
    return [
        item.strip(" ,.;:!?")
        for item in re.split(r"\s*(?:,|\band\b)\s*", cleaned, flags=re.I)
        if item.strip(" ,.;:!?")
    ]


def detect_memory_signals(text: str) -> list[MemorySignal]:
    """Deterministically retain explicit durable statements; never infer facts."""

    rules: list[tuple[re.Pattern[str], str, str]] = [
        (re.compile(r"\bI\s+(?:also\s+)?(?:know|use|learned)\s+([^.;!?]+)", re.I), "profile.skills", "add"),
        (re.compile(r"\bmy\s+skills\s+(?:include|are)\s+([^.;!?]+)", re.I), "profile.skills", "add"),
        (re.compile(r"\bI\s+(?:study|am studying|majored in|major in)\s+([^.;!?]+)", re.I), "profile.major", "replace"),
        (re.compile(r"\bmy\s+major\s+is\s+([^.;!?]+)", re.I), "profile.major", "replace"),
        (re.compile(r"\bI\s+(?:attend|study at|graduated from)\s+([^.;!?]+)", re.I), "profile.school", "replace"),
        (re.compile(r"\b(?:I\s+(?:graduate|graduated|am graduating)|my graduation year is)\s+(?:in\s+)?(20\d{2})\b", re.I), "profile.graduation_year", "replace"),
        (re.compile(r"\bI\s+((?:am\s+)?authorized to work[^.;!?]*|require sponsorship|do not require sponsorship)", re.I), "profile.work_authorization", "replace"),
        (re.compile(r"\bI\s+(?:built|created|developed)\s+([^.;!?]+)", re.I), "profile.projects", "add"),
        (re.compile(r"\bI\s+(?:worked|interned)\s+(?:at|for)\s+([^.;!?]+)", re.I), "profile.experience", "add"),
        (re.compile(r"\bI\s+no\s+longer\s+prefer\s+([^.;!?]+)", re.I), "memory.preference", "remove"),
        (re.compile(r"\bI\s+prefer\s+([^.;!?]+)", re.I), "memory.preference", "replace"),
        (re.compile(r"\bmy\s+(?:career\s+)?goal\s+is\s+([^.;!?]+)", re.I), "memory.goal", "replace"),
        (re.compile(r"\bI\s+want\s+to\s+target\s+(.+?)(?=\s+going\s+forward\b|[.;!?]|$)", re.I), "memory.goal", "replace"),
        (re.compile(r"\bI(?:'m|\s+am)\s+aiming\s+for\s+([^.;!?]+)", re.I), "memory.goal", "replace"),
        (re.compile(r"\bremember\s+that\s+my\s+(?:current\s+)?goal\s+is\s+([^.;!?]+)", re.I), "memory.goal", "replace"),
        (re.compile(r"\bI\s+(?:cannot|can't|must|need to)\s+([^.;!?]+)", re.I), "memory.constraint", "replace"),
        (re.compile(r"\bI\s+(?:just|recently)\s+([^.;!?]+)", re.I), "memory.event", "add"),
    ]
    found: list[tuple[int, MemorySignal]] = []
    for pattern, signal_type, operation in rules:
        for match in pattern.finditer(text):
            values = _values(match.group(1))
            if values:
                found.append(
                    (
                        match.start(),
                        MemorySignal(
                            type=signal_type,
                            operation_hint=operation,
                            value_hint=values,
                        ),
                    )
                )
    found.sort(key=lambda item: item[0])
    unique: list[MemorySignal] = []
    seen: set[tuple[Any, ...]] = set()
    for _, signal in found:
        key = (signal.type, signal.operation_hint, *signal.value_hint)
        if key not in seen:
            seen.add(key)
            unique.append(signal)
    return unique


def merge_memory_signals(
    classified: list[MemorySignal], explicit: list[MemorySignal]
) -> list[MemorySignal]:
    """Permit durable signals only when deterministic user evidence supports them.

    The classifier remains useful for routing, but it cannot turn an unsupported
    proposal into durable state. Missing an unusual phrasing is safer than saving
    a question, hypothetical, or assistant suggestion as a user fact.
    """

    del classified
    return list(explicit)
