from __future__ import annotations

import hashlib
import csv
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from io import StringIO
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.services.evidence import EvidenceService, evidence_service
from app.state.agent_schema import ContactChannel, PeopleCandidate, PeopleSearchRequest, PeopleSearchSufficiency, SearchPage, ToolExecutionResult
from app.tools.sources.openalex import OpenAlexAdapter
from app.tools.sources.wikidata import WikidataAdapter
from app.tools.sources.tavily import TavilyAdapter
from app.tools.sources.public_pages import PublicPageAdapter

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
    def __init__(self, repository: ProfileRepository = profile_repository, evidence: EvidenceService = evidence_service, openalex: OpenAlexAdapter | None = None, wikidata: WikidataAdapter | None = None, tavily: TavilyAdapter | None = None, public_pages: PublicPageAdapter | None = None):
        self.repository = repository
        self.evidence = evidence
        self.openalex = openalex or OpenAlexAdapter()
        self.wikidata = wikidata or WikidataAdapter()
        self.tavily = tavily or TavilyAdapter()
        self.public_pages = public_pages or PublicPageAdapter()

    def search(self, *, user_id: str, run_id: str, request: PeopleSearchRequest, source_call_budget: int | None = None) -> ToolExecutionResult:
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
        configured_max = int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12"))
        max_calls = max(0, min(configured_max, source_call_budget) if source_call_budget is not None else configured_max)
        search_session = self.repository.get_or_create_search_session(
            user_id,
            run_id,
            intent="people_search",
            normalized_request=request.model_dump(mode="json", exclude={"cursor"}),
            requested_count=request.requested_count,
            source_call_budget=max_calls,
        )
        offset = int(request.cursor or 0) if str(request.cursor or "0").isdigit() else 0
        cached = list(search_session.get("candidate_records") or [])
        if cached:
            return self._page_result(cached, request, search_session, offset=offset)
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
        planned_sources = []
        if request.person_type == "professor":
            planned_sources.append(("openalex", lambda: self.openalex.search(query=query or "faculty research", limit=request.requested_count * 2)))
        planned_sources.append(("wikidata", lambda: self.wikidata.search(query=f"{query} {request.person_type}".strip(), limit=request.requested_count * 3)))
        coverage: dict[str, Any] = {"user_connections": {"returned_count": len(records), "source_status": "available"}}
        query_hash = hashlib.sha256(query.casefold().encode()).hexdigest()
        for source_key, fetch in planned_sources:
            reservation = self.repository.reserve_search_source_calls(user_id, search_session["search_session_id"], 1)
            if not reservation["reserved_calls"]:
                coverage[source_key] = {"source_status": "budget_exhausted"}
                break
            result = fetch()
            source_calls += 1
            coverage[source_key] = {"source_status": "available" if result.ok else "unavailable", "returned_count": len(result.records)}
            self.repository.upsert_search_source_progress(
                user_id,
                search_session["search_session_id"],
                source_key=source_key,
                provider=result.source_name,
                query_hash=query_hash,
                company_or_domain=request.organization or request.school,
                visited=True,
                exhausted=not result.has_more,
                has_more=result.has_more,
                cursor=result.cursor,
                next_cursor=result.next_cursor,
                call_count=1,
                first_iteration=1,
                last_iteration=1,
                last_success_at=datetime.now(timezone.utc) if result.ok else None,
                last_error_type=result.error_type,
            )
            if result.ok:
                evidence, storage_warnings = self.evidence.store(user_id=user_id, run_id=run_id, source_type="people_source", source_name=result.source_name, source_url=result.source_url, content_type=result.content_type, raw_content=result.raw_content, structured_content={"record_count": len(result.records)})
                evidence_ids.append(evidence["evidence_id"])
                warnings.extend(storage_warnings)
                for item in result.records:
                    item.update(source_name=result.source_name, evidence_ids=[evidence["evidence_id"]])
                    records.append(item)
            else:
                warnings.append(f"{result.source_name}: source unavailable")

        tavily_enabled = os.getenv("TAVILY_ENABLED", "false").strip().casefold() == "true" and bool(os.getenv("TAVILY_API_KEY", "").strip())
        if tavily_enabled and source_calls < max_calls:
            reservation = self.repository.reserve_search_source_calls(user_id, search_session["search_session_id"], 1)
            if reservation["reserved_calls"]:
                discovery = self.tavily.search(query=f"{query} {request.person_type} official profile".strip(), max_results=min(5, request.requested_count * 2))
                source_calls += 1
                coverage["tavily"] = {"source_status": discovery.source_status, "returned_count": len(discovery.records), "discovery_only": True}
                for discovered in discovery.records:
                    if source_calls >= max_calls:
                        break
                    url = discovered.get("url")
                    host = urlsplit(url).hostname if url else None
                    if not url or not host:
                        continue
                    detail_reservation = self.repository.reserve_search_source_calls(user_id, search_session["search_session_id"], 1)
                    if not detail_reservation["reserved_calls"]:
                        break
                    detail = self.public_pages.fetch_person_detail(url=url, allowed_hosts={host})
                    source_calls += 1
                    if not detail.ok or not detail.records:
                        continue
                    evidence, storage_warnings = self.evidence.store(user_id=user_id, run_id=run_id, source_type="people_public_detail", source_name=detail.source_name, source_url=detail.source_url, content_type=detail.content_type, raw_content=detail.raw_content, structured_content={"record_count": len(detail.records)})
                    evidence_ids.append(evidence["evidence_id"])
                    warnings.extend(storage_warnings)
                    for item in detail.records:
                        item.update(source_name=detail.source_name, evidence_ids=[evidence["evidence_id"]])
                        records.append(item)
        candidates: list[PeopleCandidate] = []
        seen: set[str] = set()
        for item in records:
            name = str(item.get("name") or "").strip()
            source_url = item.get("public_source_url")
            if not name or not source_url:
                continue
            description = str(item.get("description") or item.get("current_role") or "")
            organization = item.get("organization")
            if request.person_type == "professor":
                academic_text = f"{description} {organization or ''}".casefold()
                role_supported = any(term in academic_text for term in ("professor", "faculty", "researcher", "lecturer", "academic"))
                affiliation_supported = bool(organization or item.get("affiliations"))
                if not (role_supported and affiliation_supported):
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
            private_reference = item.get("connection_id") if item.get("source_name") == "user_connection" and item.get("user_provided_email") else None
            public_contact = str(item.get("public_contact") or "").removeprefix("mailto:").strip()
            public_channels = [ContactChannel(type="email", value=public_contact, provenance="public_verified", visibility="public", evidence_id=item_evidence[0])] if public_contact and "@" in public_contact and item_evidence else []
            candidates.append(PeopleCandidate(candidate_id="person_" + hashlib.sha256(key.encode()).hexdigest()[:20], person_type=request.person_type, name=name, current_role=current_role, organization=organization, education=[str(item["education"])] if item.get("education") else [], research_topics=item.get("research_topics") or [], public_profiles=item.get("public_profiles") or [source_url], relevant_connection=overlaps, fit_explanation=fit_explanation, career_path_summary=career_path, public_source_url=source_url, contact_channels=public_channels, private_contact_reference=private_reference, evidence_ids=item_evidence, unknown_fields=[field for field in ("current_role", "organization") if not item.get(field)], source_keys=[str(item.get("source_name") or "public")]))
            if len(candidates) >= request.requested_count:
                break
        records = [item.model_dump(mode="json") for item in candidates]
        search_session = self.repository.update_search_session(
            user_id,
            search_session["search_session_id"],
            iteration=1,
            visited_sources=list(coverage),
            seen_candidate_ids=[item["candidate_id"] for item in records],
            candidate_records=records,
            source_coverage=coverage,
            consecutive_no_progress=0 if records else 1,
            status="active",
        )
        result = self._page_result(records, request, search_session, offset=offset)
        result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
        result.evidence_ids = list(dict.fromkeys([*result.evidence_ids, *evidence_ids]))
        result.source_calls = source_calls
        return result

    @staticmethod
    def _page_result(records: list[dict[str, Any]], request: PeopleSearchRequest, session: dict[str, Any], *, offset: int) -> ToolExecutionResult:
        page_size = min(request.page_size, 20)
        items = records[offset : offset + page_size]
        next_offset = offset + len(items)
        page = SearchPage[dict[str, Any]](
            items=items,
            returned_count=len(items),
            total_count=len(records),
            total_count_is_estimate=False,
            page_size=page_size,
            cursor=str(offset) if offset else None,
            next_cursor=str(next_offset) if next_offset < len(records) else None,
            has_more=next_offset < len(records),
            truncated=next_offset < len(records),
            source_coverage=dict(session.get("source_coverage") or {}),
            evidence_ids=sorted({evidence for item in items for evidence in item.get("evidence_ids") or []}),
        )
        remaining = int(session.get("remaining_source_budget") or 0)
        sufficient = len(records) >= request.requested_count
        status = PeopleSearchSufficiency(
            requested_count=request.requested_count,
            verified_count=len(records),
            unverified_count=0,
            new_candidates_this_iteration=len(records),
            remaining_source_budget=remaining,
            has_more_sources=False,
            has_more_pages=page.has_more,
            can_refine=not sufficient and remaining > 0,
            sufficient=sufficient,
            stop_reason="enough_verified_candidates" if sufficient else ("source_budget_exhausted" if not remaining else "sources_exhausted"),
        )
        return ToolExecutionResult(ok=True, data={"search_session_id": session["search_session_id"], "page": page.model_dump(mode="json"), "sufficiency": status.model_dump(mode="json")}, evidence_ids=page.evidence_ids)


people_search_service = PeopleSearchService()
