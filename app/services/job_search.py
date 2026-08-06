from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.services.evidence import EvidenceService, evidence_service
from app.state.agent_schema import JobCandidate, JobSearchRequest, SearchSufficiency, ToolExecutionResult
from app.tools.sources.catalog import CompanyCatalog
from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.lever import LeverAdapter
from app.tools.sources.public_pages import PublicPageAdapter

ELIGIBILITY_TERMS = re.compile(
    r"([^.!?]*(?:currently enrolled|student|graduat(?:e|ing|ion)|work authorization|"
    r"authorized to work|sponsorship|citizen|degree)[^.!?]*[.!?]?)",
    re.I,
)


def canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", ""))


def _normalized(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())


def extract_explicit_eligibility(description: str | None) -> str | None:
    if not description:
        return None
    matches = [" ".join(item.split()) for item in ELIGIBILITY_TERMS.findall(description)]
    return " ".join(matches[:3])[:1000] or None


def deduplicate_jobs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        keys = [
            canonical_url(item.get("application_url")),
            f"id:{item.get('source_name')}:{item.get('source_job_id')}" if item.get("source_job_id") else None,
            "|".join(_normalized(item.get(key)) for key in ("company", "title", "location")),
        ]
        if any(value in seen for value in keys if value):
            continue
        key = next((value for value in keys if value), None)
        if key is None:
            digest = hashlib.sha256(repr(sorted(item.items())).encode()).hexdigest()
            key = f"hash:{digest}"
        seen.update(value for value in keys if value)
        seen.add(key)
        result.append(item)
    return result


def apply_hard_filters(candidate: JobCandidate, request: JobSearchRequest) -> JobCandidate:
    failed: list[str] = []
    unknown: list[str] = []
    haystack = _normalized(" ".join(filter(None, [candidate.title, candidate.description_excerpt])))
    if request.employment_types:
        value = _normalized(candidate.employment_type or candidate.title)
        if not value:
            unknown.append("employment_type")
            failed.append("employment_type_unknown")
        elif not any(_normalized(item) in value for item in request.employment_types):
            failed.append("employment_type")
    if request.locations:
        if not candidate.location:
            unknown.append("location")
            failed.append("location_unknown")
        elif not any(_normalized(item) in _normalized(candidate.location) for item in request.locations):
            failed.append("location")
    if request.remote_preference and request.remote_preference.casefold() not in {"flexible", "any"}:
        remote_required = request.remote_preference.casefold() == "remote"
        is_remote = "remote" in _normalized(candidate.location) or "remote" in haystack
        if remote_required and not is_remote:
            failed.append("remote_preference")
        if not remote_required and is_remote and request.remote_preference.casefold() == "on-site":
            failed.append("remote_preference")
    if not candidate.eligibility:
        unknown.append("eligibility")
        failed.append("eligibility_unknown")
    else:
        eligibility = _normalized(candidate.eligibility)
        for requirement in request.required_eligibility:
            if _normalized(requirement) not in eligibility:
                failed.append(f"eligibility:{requirement}")
        if request.graduation_year and str(request.graduation_year) not in eligibility and "graduat" in eligibility:
            years = {int(value) for value in re.findall(r"\b20\d{2}\b", eligibility)}
            if years and request.graduation_year not in years:
                failed.append("graduation_year")
        if request.work_authorization_requirement and _normalized(request.work_authorization_requirement) not in eligibility:
            failed.append("work_authorization")
    candidate.failed_hard_constraints = sorted(set(failed))
    candidate.unknown_fields = sorted(set(candidate.unknown_fields + unknown))
    candidate.hard_constraints_met = not failed
    return candidate


class JobSearchService:
    def __init__(
        self,
        *,
        catalog: CompanyCatalog | None = None,
        greenhouse: GreenhouseAdapter | None = None,
        lever: LeverAdapter | None = None,
        public_pages: PublicPageAdapter | None = None,
        evidence: EvidenceService = evidence_service,
    ):
        self.catalog = catalog or CompanyCatalog()
        self.greenhouse = greenhouse or GreenhouseAdapter()
        self.lever = lever or LeverAdapter()
        self.public_pages = public_pages or PublicPageAdapter()
        self.evidence = evidence

    def search(
        self,
        *,
        user_id: str,
        run_id: str,
        request: JobSearchRequest,
        source_call_budget: int | None = None,
    ) -> ToolExecutionResult:
        configured_max = int(os.getenv("AGENT_MAX_SOURCE_CALLS", "12"))
        max_calls = max(
            0,
            min(configured_max, source_call_budget)
            if source_call_budget is not None
            else configured_max,
        )
        targets = []
        if request.preferred_companies:
            targets = [item for name in request.preferred_companies if (item := self.catalog.find(name)) and item.enabled]
        else:
            targets = self.catalog.enabled()
        targets = [item for item in targets if item.company.casefold() not in {name.casefold() for name in request.excluded_companies}]
        source_calls = 0
        raw_records: list[dict[str, Any]] = []
        warnings: list[str] = []
        evidence_ids: list[str] = []
        for source in targets:
            if source_calls >= max_calls:
                break
            if source.ats_type == "greenhouse" and source.board_token:
                result = self.greenhouse.search(board_token=source.board_token, company=source.company)
            elif source.ats_type == "lever" and source.lever_site:
                result = self.lever.search(site_name=source.lever_site, company=source.company)
            elif source.careers_url:
                result = self.public_pages.search(url=source.careers_url, company=source.company)
            else:
                continue
            source_calls += 1
            if not result.ok:
                warnings.append(f"{source.company}/{result.source_name}: {result.error_message}")
                continue
            evidence, storage_warnings = self.evidence.store(
                user_id=user_id,
                run_id=run_id,
                source_type="job_source",
                source_name=f"{source.company} {result.source_name}",
                source_url=result.source_url,
                content_type=result.content_type,
                raw_content=result.raw_content,
                structured_content={"record_count": len(result.records)},
            )
            warnings.extend(storage_warnings)
            evidence_ids.append(evidence["evidence_id"])
            for item in result.records:
                item.update(source_name=result.source_name, source_url=result.source_url, evidence_ids=[evidence["evidence_id"]])
                raw_records.append(item)
        normalized: list[JobCandidate] = []
        role_terms = [_normalized(item) for item in request.target_roles + request.role_keywords if item]
        for item in deduplicate_jobs(raw_records):
            text = _normalized(f"{item.get('title')} {item.get('description')}")
            if role_terms and not any(term in text for term in role_terms):
                continue
            eligibility = extract_explicit_eligibility(item.get("description"))
            identifier = item.get("source_job_id") or canonical_url(item.get("application_url")) or repr(item)
            candidate = JobCandidate(
                candidate_id="job_" + hashlib.sha256(str(identifier).encode()).hexdigest()[:20],
                source_job_id=item.get("source_job_id"),
                title=item.get("title"),
                company=item.get("company"),
                location=item.get("location"),
                employment_type=item.get("employment_type"),
                eligibility=eligibility,
                application_url=item.get("application_url"),
                source_name=item["source_name"],
                source_url=item["source_url"],
                posted_at=item.get("posted_at"),
                description_excerpt=(item.get("description") or "")[:1500] or None,
                evidence_ids=item.get("evidence_ids") or [],
                eligibility_evidence_id=(item.get("evidence_ids") or [None])[0] if eligibility else None,
            )
            profile_skills = {_normalized(item) for item in request.preferred_skills + request.required_skills}
            overlap = sorted(skill for skill in profile_skills if skill and skill in text)
            missing_required = sorted(
                skill
                for skill in request.required_skills
                if _normalized(skill) not in text
            )
            candidate.deterministic_match_features = {"skill_overlap": overlap, "role_match": any(term in _normalized(candidate.title) for term in role_terms) if role_terms else True}
            candidate.fit_score = min(100.0, 40.0 + 10.0 * len(overlap))
            candidate.transferable_skills = overlap
            candidate.skill_gaps = missing_required
            citation = (
                f" [EVIDENCE: {candidate.evidence_ids[0]}]"
                if candidate.evidence_ids
                else ""
            )
            candidate.fit_explanation = (
                "The official posting matches the requested role"
                + (f" and mentions: {', '.join(overlap)}" if overlap else "")
                + "."
                + citation
            )
            apply_hard_filters(candidate, request)
            normalized.append(candidate)
            if len(normalized) >= request.max_results:
                break
        verified = [item for item in normalized if item.hard_constraints_met]
        unverified = [item for item in normalized if not item.hard_constraints_met and "eligibility_unknown" in item.failed_hard_constraints]
        requested = request.requested_count
        stop_reason = "enough_verified_candidates" if len(verified) >= requested else ("source_budget_exhausted" if source_calls >= max_calls else "sources_exhausted")
        sufficiency = SearchSufficiency(
            requested_count=requested,
            verified_count=len(verified),
            unverified_count=len(unverified),
            remaining_source_budget=max(0, max_calls - source_calls),
            new_verified_candidates_this_iteration=len(verified),
            can_refine=len(verified) < requested and source_calls < max_calls,
            stop_reason=stop_reason,
            limiting_constraints=["eligibility"] if unverified and len(verified) < requested else [],
            suggested_relaxations=[],
        )
        return ToolExecutionResult(ok=True, data={"verified": [item.model_dump(mode="json") for item in verified[:requested]], "eligibility_not_verified": [item.model_dump(mode="json") for item in unverified[:request.max_results]], "sufficiency": sufficiency.model_dump(mode="json")}, warnings=warnings, evidence_ids=evidence_ids, source_calls=source_calls)


job_search_service = JobSearchService()
