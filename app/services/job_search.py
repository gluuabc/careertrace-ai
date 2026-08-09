from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.database.repository import ProfileRepository, profile_repository
from app.services.evidence import EvidenceService, evidence_service
from app.state.agent_schema import JobCandidate, JobSearchRequest, SearchPage, SearchSufficiency, ToolExecutionResult
from app.tools.sources.catalog import CompanyCatalog
from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.lever import LeverAdapter
from app.tools.sources.public_pages import PublicPageAdapter
from app.tools.sources.playwright import PlaywrightAdapter
from app.tools.sources.tavily import TavilyAdapter

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
        if request.student_level:
            requested_level = _normalized(request.student_level)
            level_aliases = {
                "undergraduate": ("undergraduate", "bachelor"),
                "graduate": ("graduate student", "master", "phd", "doctoral"),
                "high school": ("high school",),
            }
            expected = level_aliases.get(requested_level, (requested_level,))
            known_level_terms = {term for values in level_aliases.values() for term in values}
            if not any(term in eligibility for term in expected):
                if not any(term in eligibility for term in known_level_terms):
                    unknown.append("student_level")
                    failed.append("student_level_unknown")
                else:
                    failed.append("student_level")
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
        repository: ProfileRepository = profile_repository,
        tavily: TavilyAdapter | None = None,
        playwright: PlaywrightAdapter | None = None,
    ):
        self.catalog = catalog or CompanyCatalog()
        self.greenhouse = greenhouse or GreenhouseAdapter()
        self.lever = lever or LeverAdapter()
        self.public_pages = public_pages or PublicPageAdapter()
        self.evidence = evidence
        self.repository = repository
        self.tavily = tavily or TavilyAdapter()
        self.playwright = playwright or PlaywrightAdapter()

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
        session = self.repository.get_or_create_search_session(
            user_id,
            run_id,
            intent="job_search",
            normalized_request=request.model_dump(mode="json", exclude={"cursor"}),
            requested_count=request.requested_count,
            source_call_budget=max_calls,
        )
        offset = int(request.cursor or 0) if str(request.cursor or "0").isdigit() else 0
        cached = list(session.get("candidate_records") or [])
        if cached:
            return self._page_result(cached, request, session, offset=offset)

        targets = []
        if request.preferred_companies:
            targets = [item for name in request.preferred_companies if (item := self.catalog.find(name))]
        else:
            targets = self.catalog.enabled()
        targets = [item for item in targets if item.company.casefold() not in {name.casefold() for name in request.excluded_companies}]
        source_calls = 0
        raw_records: list[dict[str, Any]] = []
        warnings: list[str] = []
        evidence_ids: list[str] = []
        coverage: dict[str, Any] = {}
        if request.preferred_companies:
            for name in request.preferred_companies:
                if self.catalog.find(name) is None:
                    coverage[f"catalog:{name.casefold()}"] = {
                        "source_status": "known_but_unavailable",
                        "reason": "No verified catalog source is configured.",
                    }
                    warnings.append(f"{name}: no verified catalog source is configured.")
        query_hash = hashlib.sha256(
            repr(sorted(request.model_dump(mode="json", exclude={"cursor"}).items())).encode()
        ).hexdigest()
        for source in targets:
            if source_calls >= max_calls:
                break
            source_key = f"{source.ats_type or 'public'}:{source.company.casefold()}"
            if not source.enabled or source.verification_status != "verified":
                coverage[source_key] = {
                    "source_status": "known_but_unavailable",
                    "reason": "Catalog source is disabled or not verified.",
                }
                warnings.append(f"{source.company}: known source is currently unavailable.")
                self.repository.upsert_search_source_progress(
                    user_id,
                    session["search_session_id"],
                    source_key=source_key,
                    provider=source.ats_type or "public",
                    query_hash=query_hash,
                    company_or_domain=source.company,
                    visited=True,
                    exhausted=True,
                    first_iteration=1,
                    last_iteration=1,
                    last_error_type="KnownButUnavailable",
                )
                continue
            if source.ats_type == "greenhouse" and source.board_token:
                fetch = lambda: self.greenhouse.search(board_token=source.board_token, company=source.company)
            elif source.ats_type == "lever" and source.lever_site:
                fetch = lambda: self.lever.search(site_name=source.lever_site, company=source.company)
            elif source.careers_url:
                fetch = lambda: self.public_pages.search(url=source.careers_url, company=source.company)
            else:
                coverage[source_key] = {"source_status": "known_but_unavailable", "reason": "No source endpoint is configured."}
                continue
            reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
            if not reservation["reserved_calls"]:
                break
            result = fetch()
            source_calls += 1
            if (
                result.ok
                and not result.records
                and source.careers_url
                and os.getenv("PLAYWRIGHT_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}
            ):
                render_reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
                if render_reservation["reserved_calls"]:
                    host = urlsplit(source.careers_url).hostname
                    rendered = self.playwright.fetch(url=source.careers_url, company=source.company, allowed_hosts={host} if host else None)
                    source_calls += 1
                    if rendered.ok and rendered.records:
                        result = rendered
            coverage[source_key] = {
                "source_status": result.source_status or ("available" if result.ok else "unavailable"),
                "returned_count": len(result.records),
            }
            self.repository.upsert_search_source_progress(
                user_id,
                session["search_session_id"],
                source_key=source_key,
                provider=result.source_name,
                query_hash=query_hash,
                company_or_domain=source.company,
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
            if not result.ok:
                warnings.append(f"{source.company}/{result.source_name}: source unavailable")
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

        tavily_enabled = os.getenv("TAVILY_ENABLED", "false").strip().casefold() == "true" and bool(os.getenv("TAVILY_API_KEY", "").strip())
        if tavily_enabled and source_calls < max_calls:
            reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
            if reservation["reserved_calls"]:
                query = " ".join([*(request.target_roles or request.role_keywords), *(request.preferred_companies or []), "jobs careers"])
                domains = []
                for source in targets:
                    candidate_url = source.official_source_url or source.careers_url
                    host = urlsplit(candidate_url).hostname if candidate_url else None
                    if host:
                        domains.append(host)
                discovery = self.tavily.search(query=query, max_results=min(5, request.max_results), include_domains=domains or None)
                source_calls += 1
                coverage["tavily"] = {"source_status": discovery.source_status, "returned_count": len(discovery.records), "discovery_only": True}
                for discovered in discovery.records:
                    if source_calls >= max_calls:
                        break
                    url = discovered.get("url")
                    if not url:
                        continue
                    detail_reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
                    if not detail_reservation["reserved_calls"]:
                        break
                    host = urlsplit(url).hostname
                    detail = self.public_pages.fetch_job_detail(url=url, allowed_hosts={host} if host else None)
                    source_calls += 1
                    if not detail.ok:
                        continue
                    evidence, storage_warnings = self.evidence.store(
                        user_id=user_id,
                        run_id=run_id,
                        source_type="job_detail",
                        source_name=detail.source_name,
                        source_url=detail.source_url,
                        content_type=detail.content_type,
                        raw_content=detail.raw_content,
                        structured_content=detail.records[0] if detail.records else None,
                    )
                    warnings.extend(storage_warnings)
                    evidence_ids.append(evidence["evidence_id"])
                    for item in detail.records:
                        item.update(source_name=detail.source_name, source_url=detail.source_url, evidence_ids=[evidence["evidence_id"]])
                        raw_records.append(item)
        normalized: list[JobCandidate] = []
        role_terms = [_normalized(item) for item in request.target_roles + request.role_keywords if item]
        for item in deduplicate_jobs(raw_records):
            text = _normalized(f"{item.get('title')} {item.get('description')}")
            if role_terms and not any(term in text for term in role_terms):
                continue
            eligibility = item.get("eligibility") or extract_explicit_eligibility(item.get("description"))
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
                source_keys=[f"{item['source_name']}:{_normalized(item.get('company'))}"],
            )
            profile_skills = {_normalized(item) for item in request.profile_skills}
            overlap = sorted(skill for skill in profile_skills if skill and skill in text)
            missing_required = sorted(
                skill
                for skill in request.desired_job_skills
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
        records = [item.model_dump(mode="json") for item in [*verified, *unverified]]
        session = self.repository.update_search_session(
            user_id,
            session["search_session_id"],
            iteration=1,
            visited_sources=list(coverage),
            seen_candidate_ids=[item["candidate_id"] for item in records],
            candidate_records=records,
            source_coverage=coverage,
            consecutive_no_progress=0 if records else 1,
            status="active",
        )
        result = self._page_result(records, request, session, offset=offset)
        result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
        result.evidence_ids = list(dict.fromkeys([*result.evidence_ids, *evidence_ids]))
        result.source_calls = source_calls
        if isinstance(result.data, dict):
            result.data["sufficiency"] = sufficiency.model_dump(mode="json")
        return result

    @staticmethod
    def _page_result(
        records: list[dict[str, Any]],
        request: JobSearchRequest,
        session: dict[str, Any],
        *,
        offset: int,
    ) -> ToolExecutionResult:
        page_size = min(request.page_size, 20)
        page_records = records[offset : offset + page_size]
        summaries = []
        for item in page_records:
            summary = {
                key: item.get(key)
                for key in (
                    "candidate_id", "title", "company", "location", "employment_type",
                    "eligibility", "application_url", "source_name", "source_url",
                    "description_excerpt", "hard_constraints_met", "failed_hard_constraints",
                    "unknown_fields", "evidence_ids", "fit_score", "fit_explanation",
                    "transferable_skills", "skill_gaps", "first_seen_iteration",
                    "last_seen_iteration", "source_keys",
                )
            }
            excerpt = summary.get("description_excerpt")
            if excerpt:
                summary["description_excerpt"] = str(excerpt)[:300]
            summaries.append(summary)
        next_offset = offset + len(summaries)
        page = SearchPage[dict[str, Any]](
            items=summaries,
            returned_count=len(summaries),
            total_count=len(records),
            total_count_is_estimate=False,
            page_size=page_size,
            cursor=str(offset) if offset else None,
            next_cursor=str(next_offset) if next_offset < len(records) else None,
            has_more=next_offset < len(records),
            truncated=next_offset < len(records),
            source_coverage=dict(session.get("source_coverage") or {}),
            evidence_ids=sorted({evidence for item in summaries for evidence in item.get("evidence_ids") or []}),
            warnings=[],
        )
        verified_count = sum(bool(item.get("hard_constraints_met")) for item in records)
        sufficiency = SearchSufficiency(
            requested_count=request.requested_count,
            verified_count=verified_count,
            unverified_count=len(records) - verified_count,
            remaining_source_budget=int(session.get("remaining_source_budget") or 0),
            new_verified_candidates_this_iteration=verified_count,
            can_refine=verified_count < request.requested_count and int(session.get("remaining_source_budget") or 0) > 0,
            stop_reason="enough_verified_candidates" if verified_count >= request.requested_count else "sources_exhausted",
        )
        return ToolExecutionResult(
            ok=True,
            data={
                "search_session_id": session["search_session_id"],
                "page": page.model_dump(mode="json"),
                "sufficiency": sufficiency.model_dump(mode="json"),
            },
            evidence_ids=page.evidence_ids,
            source_calls=0,
        )


job_search_service = JobSearchService()
