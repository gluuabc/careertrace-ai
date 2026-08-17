from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from app.database.database import PROJECT_ROOT


FIXTURE_ROOT = PROJECT_ROOT / "demo" / "search_fixtures"
MIN_USEFUL_LIVE_RESULTS = 3


def should_use_demo_fallback(
    *,
    judge_mode: bool,
    useful_live_count: int,
    elapsed_seconds: float,
    provider_timed_out: bool = False,
    soft_trigger_seconds: float = 10.0,
) -> bool:
    """Return whether an explicitly labeled snapshot may complete a judge search."""

    return bool(
        judge_mode
        and useful_live_count < MIN_USEFUL_LIVE_RESULTS
        and (provider_timed_out or elapsed_seconds >= soft_trigger_seconds)
    )


def load_demo_search_fixtures(
    kind: Literal["jobs", "people"], *, root: Path = FIXTURE_ROOT
) -> list[dict[str, Any]]:
    """Load the manifest-selected public-source snapshot and reject unsafe records."""

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    relative_path = str(manifest[kind]["file"])
    fixture_path = (root / relative_path).resolve()
    if fixture_path.parent != root.resolve():
        raise ValueError("Demo fixture path must remain inside the fixture directory.")
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Demo fixture must contain a JSON list.")
    safe: list[dict[str, Any]] = []
    for raw in records:
        item = dict(raw)
        source_url = str(item.get("source_url") or item.get("public_source_url") or "")
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if not item.get("is_demo_sample") or not item.get("snapshot_date"):
            continue
        item["is_demo_sample"] = True
        item["source_status"] = "demo_snapshot"
        safe.append(item)
    return safe

