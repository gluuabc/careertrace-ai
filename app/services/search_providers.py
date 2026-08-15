from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from time import perf_counter
from typing import Any

from app.tools.sources.base import SourceResult


MAX_PROVIDER_CONCURRENCY = 4
JUDGE_SOFT_FALLBACK_SECONDS = 10.0
JUDGE_HARD_SEARCH_SECONDS = 12.0


def run_provider_fetches(
    tasks: list[tuple[str, str, Any]], *, timeout_seconds: float | None = None
) -> dict[str, tuple[SourceResult, int, bool]]:
    """Run network providers concurrently and preserve bounded partial results."""

    if not tasks:
        return {}
    executor = ThreadPoolExecutor(max_workers=min(MAX_PROVIDER_CONCURRENCY, len(tasks)))

    def invoke(callable_: Any) -> tuple[SourceResult, int]:
        started = perf_counter()
        result = callable_()
        return result, round((perf_counter() - started) * 1000)

    future_by_key = {
        key: (provider, executor.submit(invoke, callable_))
        for key, provider, callable_ in tasks
    }
    done, _pending = wait(
        [item[1] for item in future_by_key.values()], timeout=timeout_seconds
    )
    output: dict[str, tuple[SourceResult, int, bool]] = {}
    for key, (provider, future) in future_by_key.items():
        if future in done:
            try:
                result, duration_ms = future.result()
                output[key] = (result, duration_ms, False)
            except Exception as error:
                output[key] = (
                    SourceResult(False, provider, error_type=type(error).__name__),
                    0,
                    False,
                )
        else:
            future.cancel()
            output[key] = (
                SourceResult(
                    False,
                    provider,
                    error_type="ProviderTimeout",
                    source_status="timeout",
                ),
                round((timeout_seconds or 0) * 1000),
                True,
            )
    executor.shutdown(wait=False, cancel_futures=True)
    return output

