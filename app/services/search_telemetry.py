from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.database.repository import ProfileRepository


@dataclass
class SearchPhase:
    phase: str
    provider: str | None = None
    candidate_count: int | None = None
    success: bool = True
    timed_out: bool = False
    embedding_count: int = 0
    embedding_cache_hit_count: int = 0
    metadata: dict[str, int] = field(default_factory=dict)


class _MeasuredPhase:
    def __init__(self, recorder: "SearchTelemetryRecorder", phase: SearchPhase):
        self.recorder = recorder
        self.phase = phase
        self.started = 0.0

    def __enter__(self) -> SearchPhase:
        self.started = perf_counter()
        return self.phase

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if exc_type is not None:
            self.phase.success = False
        self.recorder.record(
            self.phase,
            duration_ms=max(0, round((perf_counter() - self.started) * 1000)),
        )
        return False


class SearchTelemetryRecorder:
    """Persist bounded search metadata without raw requests or source content."""

    def __init__(
        self,
        repository: ProfileRepository,
        *,
        user_id: str,
        run_id: str,
        search_session_id: str,
    ):
        self.repository = repository
        self.user_id = user_id
        self.run_id = run_id
        self.search_session_id = search_session_id

    def measure(self, phase: str, *, provider: str | None = None) -> _MeasuredPhase:
        return _MeasuredPhase(self, SearchPhase(phase=phase, provider=provider))

    def record(self, phase: SearchPhase, *, duration_ms: int) -> None:
        try:
            self.repository.create_search_phase_metric(
                self.user_id,
                run_id=self.run_id,
                search_session_id=self.search_session_id,
                phase=phase.phase,
                provider=phase.provider,
                duration_ms=duration_ms,
                candidate_count=phase.candidate_count,
                success=phase.success,
                timed_out=phase.timed_out,
                embedding_count=phase.embedding_count,
                embedding_cache_hit_count=phase.embedding_cache_hit_count,
                metadata_json=phase.metadata,
            )
        except Exception:
            # Search remains available if optional observability persistence fails.
            pass

    def observe(self, phase: str, duration_ms: int, **values: Any) -> None:
        item = SearchPhase(phase=phase)
        for key, value in values.items():
            if hasattr(item, key):
                setattr(item, key, value)
        self.record(item, duration_ms=max(0, int(duration_ms)))
