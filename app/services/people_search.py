from __future__ import annotations

import hashlib
import csv
import json
from io import StringIO
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.services.evidence import EvidenceService, evidence_service
from app.state.agent_schema import PeopleCandidate, PeopleSearchRequest, ToolExecutionResult
from app.tools.sources.openalex import OpenAlexAdapter
from app.tools.sources.wikidata import WikidataAdapter

RECRUITER_TERMS = ("recruiter", "recruiting", "talent acquisition", "university recruiting", "campus recruiting", "technical recruiting")
CONNECTION_FIELDS = {"name", "current_role", "organization", "education", "graduation_year", "public_profile_url", "user_provided_email", "notes"}


def validate_connection_csv(content: str, *, max_rows: int = 500, max_field_length: int = 2000) -> tuple[list[dict[str, Any]], list[str]]:
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames or "name" not in reader.fieldnames:
        return [], ["CSV requires a name column."]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, raw in enumerate(reader, start=2):
        if len(rows) >= max_rows:
            errors.append(f"Row {number}: row limit exceeded.")
            break
        item: dict[str, Any] = {}
        for key in CONNECTION_FIELDS:
            value = str(raw.get(key) or "").strip()
            if value.startswith(("=", "+", "-", "@")):
                errors.append(f"Row {number}, {key}: executable spreadsheet formula rejected.")
                value = ""
            if len(value) > max_field_length:
                errors.append(f"Row {number}, {key}: field exceeds {max_field_length} characters.")
                value = value[:max_field_length]
            item[key] = value or None
        if not item["name"]:
            errors.append(f"Row {number}: name is required.")
            continue
        if item["graduation_year"]:
            try:
                item["graduation_year"] = int(item["graduation_year"])
            except ValueError:
                errors.append(f"Row {number}: graduation_year must be a number.")
                continue
        item["source_type"] = "csv"
        rows.append(item)
    return rows, errors


class PeopleSearchService:
    def __init__(self, repository: ProfileRepository = profile_repository, evidence: EvidenceService = evidence_service, openalex: OpenAlexAdapter | None = None, wikidata: WikidataAdapter | None = None):
        self.repository = repository
        self.evidence = evidence
        self.openalex = openalex or OpenAlexAdapter()
        self.wikidata = wikidata or WikidataAdapter()

    def search(self, *, user_id: str, run_id: str, request: PeopleSearchRequest) -> ToolExecutionResult:
        if request.person_type == "alumni" and not request.school:
            return ToolExecutionResult(
                ok=False,
                error_type="MissingRequiredField",
                error_message="Alumni search requires a target school.",
            )
        if request.person_type == "recruiter" and not request.organization:
            return ToolExecutionResult(
                ok=False,
                error_type="MissingRequiredField",
                error_message="Recruiter search requires a target organization.",
            )
        profile = self.repository.get_profile(user_id) or {}
        records: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        warnings: list[str] = []
        source_calls = 0
        for item in self.repository.list_connections(user_id):
            if not item.get("public_profile_url"):
                continue
            if request.organization and request.organization.casefold() not in str(item.get("organization") or "").casefold():
                continue
            public_record = {
                key: item.get(key)
                for key in (
                    "name",
                    "current_role",
                    "organization",
                    "education",
                    "graduation_year",
                    "public_profile_url",
                )
            }
            evidence, storage_warnings = self.evidence.store(
                user_id=user_id,
                run_id=run_id,
                source_type="user_provided_public_reference",
                source_name="user_connection",
                source_url=item["public_profile_url"],
                content_type="application/json",
                raw_content=json.dumps(public_record, ensure_ascii=False),
                structured_content=public_record,
            )
            evidence_ids.append(evidence["evidence_id"])
            warnings.extend(storage_warnings)
            records.append(
                {
                    **item,
                    "public_source_url": item["public_profile_url"],
                    "public_profiles": [item["public_profile_url"]],
                    "source_name": "user_connection",
                    "evidence_ids": [evidence["evidence_id"]],
                }
            )
        query_parts = [request.organization or "", request.school or "", *request.research_topics, *request.role_keywords]
        query = " ".join(item for item in query_parts if item).strip()
        results = []
        if request.person_type == "professor":
            results.append(
                self.openalex.search(
                    query=query or "professor", limit=request.requested_count * 2
                )
            )
        results.append(
            self.wikidata.search(
                query=f"{query} {request.person_type}".strip(),
                limit=request.requested_count * 3,
            )
        )
        for result in results:
            source_calls += 1
            if result.ok:
                evidence, storage_warnings = self.evidence.store(user_id=user_id, run_id=run_id, source_type="people_source", source_name=result.source_name, source_url=result.source_url, content_type=result.content_type, raw_content=result.raw_content, structured_content={"record_count": len(result.records)})
                evidence_ids.append(evidence["evidence_id"])
                warnings.extend(storage_warnings)
                for item in result.records:
                    item.update(source_name=result.source_name, evidence_ids=[evidence["evidence_id"]])
                    records.append(item)
            else:
                warnings.append(f"{result.source_name}: {result.error_message}")
        candidates: list[PeopleCandidate] = []
        seen: set[str] = set()
        for item in records:
            name = str(item.get("name") or "").strip()
            source_url = item.get("public_source_url")
            if not name or not source_url:
                continue
            description = str(item.get("description") or item.get("current_role") or "")
            organization = item.get("organization")
            if request.person_type == "professor" and "professor" not in description.casefold():
                continue
            if request.person_type == "alumni" and request.school.casefold() not in str(
                item.get("education") or description
            ).casefold():
                continue
            if request.person_type == "recruiter":
                if not any(term in description.casefold() for term in RECRUITER_TERMS):
                    continue
                if request.organization and request.organization.casefold() not in str(organization or description).casefold():
                    continue
            key = f"{name.casefold()}|{str(organization).casefold()}|{source_url}"
            if key in seen:
                continue
            seen.add(key)
            overlaps = []
            if profile.get("school") and profile["school"].casefold() in str(item.get("education") or "").casefold():
                overlaps.append(f"Same university: {profile['school']}")
            for topic in item.get("research_topics") or []:
                if any(str(skill).casefold() in str(topic).casefold() for skill in profile.get("skills") or []):
                    overlaps.append(f"Shared topic: {topic}")
            item_evidence = item.get("evidence_ids") or []
            citation = f" [EVIDENCE: {item_evidence[0]}]" if item_evidence else ""
            current_role = item.get("current_role") or description or None
            fit_explanation = (
                "; ".join(overlaps)
                if overlaps
                else f"Public source supports this {request.person_type} search result."
            ) + citation
            career_path = (
                "Current public role: "
                + str(current_role or "unknown")
                + (f" at {organization}" if organization else "")
                + "."
                + citation
            )
            candidates.append(PeopleCandidate(candidate_id="person_" + hashlib.sha256(key.encode()).hexdigest()[:20], person_type=request.person_type, name=name, current_role=current_role, organization=organization, education=[str(item["education"])] if item.get("education") else [], research_topics=item.get("research_topics") or [], public_profiles=item.get("public_profiles") or [source_url], relevant_connection=overlaps, fit_explanation=fit_explanation, career_path_summary=career_path, public_source_url=source_url, public_contact=item.get("user_provided_email") if item.get("source_name") == "user_connection" else None, contact_status="available" if item.get("user_provided_email") else "unavailable", evidence_ids=item_evidence, unknown_fields=[field for field in ("current_role", "organization") if not item.get(field)]))
            if len(candidates) >= request.requested_count:
                break
        return ToolExecutionResult(ok=True, data={"candidates": [item.model_dump(mode="json") for item in candidates], "partial": len(candidates) < request.requested_count}, warnings=warnings, evidence_ids=evidence_ids, source_calls=source_calls)


people_search_service = PeopleSearchService()
