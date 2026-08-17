from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.openalex import OpenAlexAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _job_snapshot(limit: int) -> list[dict[str, Any]]:
    adapter = GreenhouseAdapter()
    records: list[dict[str, Any]] = []
    catalog = yaml.safe_load(
        (PROJECT_ROOT / "config" / "job_sources.yaml").read_text(encoding="utf-8")
    ) or {}
    for source in catalog.get("companies") or []:
        if (
            not source.get("enabled")
            or source.get("verification_status") != "verified"
            or source.get("ats_type") != "greenhouse"
            or not source.get("board_token")
        ):
            continue
        result = adapter.search(
            board_token=str(source["board_token"]), company=str(source["company"])
        )
        if not result.ok:
            continue
        for raw in result.records:
            url = raw.get("application_url")
            if not url or not re.search(
                r"engineer|machine learning|data science|intern",
                str(raw.get("title") or ""),
                re.I,
            ):
                continue
            records.append(
                {
                    "candidate_id": f"demo_job_{raw.get('source_job_id') or len(records)}",
                    "source_job_id": raw.get("source_job_id"),
                    "title": raw.get("title"),
                    "company": raw.get("company"),
                    "location": raw.get("location"),
                    "application_url": url,
                    "source_url": url,
                    "source_name": "Greenhouse public board snapshot",
                    "description_excerpt": str(raw.get("description") or "")[:300],
                    "hard_constraints_met": False,
                    "failed_hard_constraints": ["snapshot_not_current"],
                    "unknown_fields": ["current_availability", "requirements"],
                    "verification_status": "requirements_not_fully_verified",
                    "requirement_status": "requirements_not_fully_verified",
                    "source_status": "demo_snapshot",
                    "is_demo_sample": True,
                    "snapshot_date": date.today().isoformat(),
                    "source_verified_at_snapshot": True,
                }
            )
            if len(records) >= limit:
                return records
    return records


def _people_snapshot(limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    adapter = OpenAlexAdapter()
    for query in ("Yoshua Bengio", "Andrew Ng", "Daphne Koller", "Fei-Fei Li"):
        result = adapter.search(query=query, limit=1)
        if not result.ok:
            continue
        for raw in result.records[:1]:
            url = raw.get("public_source_url")
            if not url or not raw.get("name"):
                continue
            records.append(
                {
                    "candidate_id": f"demo_person_{str(url).rsplit('/', 1)[-1]}",
                    "person_type": "professor",
                    "name": raw["name"],
                    "current_role": raw.get("current_role"),
                    "organization": raw.get("organization"),
                    "research_topics": raw.get("research_topics") or [],
                    "public_profiles": [url],
                    "public_source_url": url,
                    "source_keys": ["openalex_snapshot"],
                    "verification_status": "insufficient_public_evidence",
                    "identity_confidence": "unverified",
                    "source_status": "demo_snapshot",
                    "is_demo_sample": True,
                    "snapshot_date": date.today().isoformat(),
                    "source_verified_at_snapshot": True,
                }
            )
            if len(records) >= limit:
                return records
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Build bounded public-source judge snapshots.")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "demo" / "search_fixtures")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    limit = min(max(args.limit, 3), 20)
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot_date = date.today().isoformat()
    jobs_file = f"jobs-{snapshot_date}.json"
    people_file = f"people-{snapshot_date}.json"
    (args.output / jobs_file).write_text(json.dumps(_job_snapshot(limit), indent=2), encoding="utf-8")
    (args.output / people_file).write_text(json.dumps(_people_snapshot(limit), indent=2), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "description": "Public-source snapshots for judge-mode fallback only. These records are not claims about current availability or current person state.",
        "jobs": {"file": jobs_file, "generated_by": "scripts/build_demo_search_fixtures.py"},
        "people": {"file": people_file, "generated_by": "scripts/build_demo_search_fixtures.py"},
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
