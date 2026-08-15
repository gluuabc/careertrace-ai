from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.database.repository import ProfileRepository, profile_repository
from app.services.evidence import EvidenceService, evidence_service
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval import HybridRetrievalService
from app.services.retrieval_corpus import RetrievalCorpusIndexer
from app.state.agent_schema import JobCandidate, JobSearchRequest, RequirementState, RequirementStatus, SearchPage, SearchSufficiency, SourceStatus, ToolExecutionResult
from app.services.search_telemetry import SearchTelemetryRecorder
from app.services.demo_search_fixtures import (
    load_demo_search_fixtures,
    should_use_demo_fallback,
)
from app.services.search_providers import (
    JUDGE_HARD_SEARCH_SECONDS,
    run_provider_fetches,
)
from app.tools.sources.catalog import CompanyCatalog
from app.tools.sources.greenhouse import GreenhouseAdapter
from app.tools.sources.lever import LeverAdapter
from app.tools.sources.public_pages import PublicPageAdapter
from app.tools.sources.playwright import PlaywrightAdapter
from app.tools.sources.tavily import TavilyAdapter
from app.tools.sources.trust import assess_job_source

ELIGIBILITY_KEYWORDS = re.compile(
    r"currently enrolled|student|graduat(?:e|ing|ion)|work authorization|"
    r"authorized to work|sponsorship|citizen|degree",
    re.I,
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
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
    bounded = description[:20_000]
    matches = [
        " ".join(sentence.split())
        for sentence in SENTENCE_BOUNDARY.split(bounded)
        if ELIGIBILITY_KEYWORDS.search(sentence)
    ]
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


def lexical_job_shortlist(candidates: list[JobCandidate], request: JobSearchRequest, *, limit: int = 30) -> list[JobCandidate]:
    terms = [_normalized(item) for item in [*request.target_roles, *request.role_keywords, *request.desired_job_skills, *request.profile_skills, *request.industries] if _normalized(item)]
    def score(candidate: JobCandidate) -> tuple[int, str]:
        text = _normalized(" ".join(filter(None, [candidate.title, candidate.company, candidate.location, candidate.description_excerpt])))
        return (-sum(term in text for term in terms), candidate.candidate_id)
    return sorted(candidates, key=score)[: min(max(1, limit), 30)]


SKILL_TERMS = (
    "python", "java", "javascript", "typescript", "sql", "aws", "docker",
    "kubernetes", "react", "pytorch", "tensorflow", "machine learning",
)


def extract_explicit_job_skills(description: str, request: JobSearchRequest) -> tuple[list[str], list[str]]:
    """Return only skills literally present in explicit required/preferred sections."""
    candidates: list[str] = []
    seen_candidates: set[str] = set()
    for skill in [*request.profile_skills, *request.desired_job_skills, *SKILL_TERMS]:
        key = _normalized(skill)
        if key and key not in seen_candidates:
            seen_candidates.add(key)
            candidates.append(skill)
    sections = re.split(r"(?im)^\s*(requirements?|qualifications?|preferred qualifications?|bonus)\s*:?[\s]*$", description or "")
    required_text = description or ""
    preferred_text = ""
    if len(sections) > 1:
        required_parts: list[str] = []
        preferred_parts: list[str] = []
        for index in range(1, len(sections), 2):
            heading = sections[index].casefold()
            body = sections[index + 1] if index + 1 < len(sections) else ""
            (preferred_parts if "preferred" in heading or "bonus" in heading else required_parts).append(body)
        required_text = "\n".join(required_parts)
        preferred_text = "\n".join(preferred_parts)
    required = [skill for skill in candidates if _normalized(skill) and _normalized(skill) in _normalized(required_text)]
    preferred = [skill for skill in candidates if _normalized(skill) and _normalized(skill) in _normalized(preferred_text)]
    return list(dict.fromkeys(required)), list(dict.fromkeys(preferred))


def apply_hard_filters(candidate: JobCandidate, request: JobSearchRequest) -> JobCandidate:
    states: dict[str, RequirementState] = {}
    haystack = _normalized(" ".join(filter(None, [candidate.title, candidate.description_excerpt])))
    def set_state(field: str, state: RequirementState) -> None:
        states[field] = state

    excluded = {_normalized(item) for item in request.excluded_companies}
    if excluded:
        set_state("excluded_company", RequirementState.CONFLICT if _normalized(candidate.company) in excluded else RequirementState.MATCH)
    if request.employment_types:
        value = _normalized(candidate.employment_type)
        if not value:
            set_state("employment_type", RequirementState.UNKNOWN)
        elif not any(_normalized(item) in value for item in request.employment_types):
            set_state("employment_type", RequirementState.CONFLICT)
        else:
            set_state("employment_type", RequirementState.MATCH)
    if request.locations:
        if not candidate.location:
            set_state("location", RequirementState.UNKNOWN)
        else:
            location = _normalized(candidate.location)
            if any(_normalized(item) in location for item in request.locations):
                set_state("location", RequirementState.MATCH)
            elif any(
                term in location
                for term in ("multiple locations", "various locations", "nationwide", "united states")
            ):
                set_state("location", RequirementState.UNKNOWN)
            else:
                set_state("location", RequirementState.CONFLICT)
    requested_roles = _normalized(" ".join(request.target_roles))
    candidate_title = _normalized(candidate.title)
    junior_request = any(term in requested_roles for term in ("intern", "junior", "entry level", "new grad"))
    senior_request = any(term in requested_roles for term in ("senior", "staff", "principal", "lead", "director"))
    junior_candidate = any(term in candidate_title for term in ("intern", "junior", "entry level", "new grad"))
    senior_candidate = any(term in candidate_title for term in ("senior", "staff", "principal", "lead", "director"))
    if (junior_request and senior_candidate) or (senior_request and junior_candidate):
        set_state("seniority", RequirementState.CONFLICT)
    if request.remote_preference and request.remote_preference.casefold() not in {"flexible", "any"}:
        preference = request.remote_preference.casefold()
        is_remote = "remote" in _normalized(candidate.location) or "remote" in haystack
        is_onsite = any(term in haystack for term in ("on-site", "onsite", "in person"))
        if preference == "remote":
            set_state("remote_preference", RequirementState.MATCH if is_remote else (RequirementState.CONFLICT if is_onsite else RequirementState.UNKNOWN))
        elif preference in {"on-site", "onsite"}:
            set_state("remote_preference", RequirementState.MATCH if is_onsite else (RequirementState.CONFLICT if is_remote else RequirementState.UNKNOWN))
    if not candidate.eligibility:
        set_state("eligibility", RequirementState.UNKNOWN)
        if request.student_level:
            set_state("student_level", RequirementState.UNKNOWN)
        if request.graduation_year:
            set_state("graduation_year", RequirementState.UNKNOWN)
        if request.work_authorization_requirement:
            set_state("work_authorization", RequirementState.UNKNOWN)
    else:
        eligibility = _normalized(candidate.eligibility)
        if request.required_eligibility:
            matches = [_normalized(item) in eligibility for item in request.required_eligibility]
            set_state("eligibility", RequirementState.MATCH if all(matches) else RequirementState.UNKNOWN)
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
                    set_state("student_level", RequirementState.UNKNOWN)
                else:
                    set_state("student_level", RequirementState.CONFLICT)
            else:
                set_state("student_level", RequirementState.MATCH)
        if request.graduation_year:
            years = {int(value) for value in re.findall(r"\b20\d{2}\b", eligibility)}
            if not years:
                set_state("graduation_year", RequirementState.UNKNOWN)
            else:
                set_state("graduation_year", RequirementState.MATCH if request.graduation_year in years else RequirementState.CONFLICT)
        if request.work_authorization_requirement:
            required = _normalized(request.work_authorization_requirement)
            if required in eligibility:
                set_state("work_authorization", RequirementState.MATCH)
            else:
                user_needs_sponsorship = (
                    "require sponsorship" in required
                    or "need sponsorship" in required
                ) and "do not" not in required
                user_does_not_need_sponsorship = any(
                    term in required
                    for term in ("do not require sponsorship", "no sponsorship", "authorized to work")
                )
                posting_denies_sponsorship = any(
                    term in eligibility
                    for term in ("no sponsorship", "not sponsor", "without sponsorship", "sponsorship is not available")
                )
                posting_requires_sponsorship = "sponsorship required" in eligibility
                posting_offers_sponsorship = any(
                    term in eligibility
                    for term in ("sponsorship available", "sponsorship provided", "will sponsor")
                )
                if user_needs_sponsorship and posting_denies_sponsorship:
                    set_state("work_authorization", RequirementState.CONFLICT)
                elif user_needs_sponsorship and posting_offers_sponsorship:
                    set_state("work_authorization", RequirementState.MATCH)
                elif user_does_not_need_sponsorship and posting_requires_sponsorship:
                    set_state("work_authorization", RequirementState.CONFLICT)
                elif user_does_not_need_sponsorship and (
                    "authorized to work" in eligibility or posting_denies_sponsorship
                ):
                    set_state("work_authorization", RequirementState.MATCH)
                else:
                    set_state("work_authorization", RequirementState.UNKNOWN)
    if "industries" in request.hard_preference_fields:
        if not request.industries:
            set_state("industries", RequirementState.UNKNOWN)
        else:
            set_state("industries", RequirementState.MATCH if any(_normalized(item) in haystack for item in request.industries) else RequirementState.UNKNOWN)
    if "salary_preference" in request.hard_preference_fields:
        set_state("salary_preference", RequirementState.UNKNOWN if not candidate.salary else (RequirementState.MATCH if _normalized(request.salary_preference) in _normalized(candidate.salary) else RequirementState.CONFLICT))
    conflicts = sorted(field for field, state in states.items() if state == RequirementState.CONFLICT)
    unknown = sorted(field for field, state in states.items() if state == RequirementState.UNKNOWN)
    candidate.hard_requirement_states = states
    candidate.failed_hard_constraints = sorted([*conflicts, *(f"{field}_unknown" for field in unknown)])
    candidate.unknown_fields = sorted(set(candidate.unknown_fields + unknown))
    candidate.hard_constraints_met = not conflicts and not unknown
    candidate.verification_status = "conflict" if conflicts else ("requirements_not_fully_verified" if unknown else "verified")
    candidate.requirement_status = RequirementStatus.CONFLICT if conflicts else (RequirementStatus.REQUIREMENTS_NOT_FULLY_VERIFIED if unknown else RequirementStatus.MATCHES)
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
        retrieval: HybridRetrievalService | None = None,
        indexer: RetrievalCorpusIndexer | None = None,
    ):
        self.catalog = catalog or CompanyCatalog()
        self.greenhouse = greenhouse or GreenhouseAdapter()
        self.lever = lever or LeverAdapter()
        self.public_pages = public_pages or PublicPageAdapter()
        self.evidence = evidence
        self.repository = repository
        self.tavily = tavily or TavilyAdapter()
        self.playwright = playwright or PlaywrightAdapter()
        retrieval_repository = RetrievalRepository(repository.session_factory)
        self.retrieval = retrieval or HybridRetrievalService(retrieval_repository)
        self.indexer = indexer or RetrievalCorpusIndexer(retrieval_repository, self.retrieval)

    def search(
        self,
        *,
        user_id: str,
        run_id: str,
        request: JobSearchRequest,
        source_call_budget: int | None = None,
    ) -> ToolExecutionResult:
        search_started = perf_counter()
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
        telemetry = SearchTelemetryRecorder(
            self.repository,
            user_id=user_id,
            run_id=run_id,
            search_session_id=session["search_session_id"],
        )
        offset = int(request.cursor or 0) if str(request.cursor or "0").isdigit() else 0
        cached = list(session.get("candidate_records") or [])
        if cached and request.cursor is not None:
            result = self._page_result(cached, request, session, offset=offset)
            telemetry.observe(
                "search_tool",
                round((perf_counter() - search_started) * 1000),
                candidate_count=len(result.data["page"]["items"]),
            )
            return result
        iteration = int(session.get("iteration") or 0) + 1
        existing_candidates = [JobCandidate.model_validate(item) for item in cached]

        targets = []
        if request.preferred_companies:
            targets = [item for name in request.preferred_companies if (item := self.catalog.find(name))]
        else:
            targets = self.catalog.enabled()
        targets = [item for item in targets if item.company.casefold() not in {name.casefold() for name in request.excluded_companies}]
        approved_job_hosts = {
            host.casefold()
            for item in targets
            for candidate_url in (item.official_source_url, item.careers_url)
            if candidate_url and (host := urlsplit(candidate_url).hostname)
        }
        progressed = {item["source_key"]: item for item in session.get("sources") or []}
        targets = [
            item for item in targets
            if not progressed.get(f"{item.ats_type or 'public'}:{item.company.casefold()}", {}).get("exhausted")
        ]
        targets = targets[: max(1, int(os.getenv("SEARCH_SOURCES_PER_ITERATION", "2")))]
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
        judge_mode = bool(self.repository.get_user(user_id).get("is_demo"))
        fetch_tasks: list[tuple[str, str, Any]] = []
        sources_by_key = {}
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
                    first_iteration=iteration,
                    last_iteration=iteration,
                    last_error_type="KnownButUnavailable",
                )
                continue
            if source.ats_type == "greenhouse" and source.board_token:
                fetch = lambda source=source: self.greenhouse.search(board_token=source.board_token, company=source.company)
            elif source.ats_type == "lever" and source.lever_site:
                fetch = lambda source=source: self.lever.search(site_name=source.lever_site, company=source.company)
            elif source.careers_url:
                fetch = lambda source=source: self.public_pages.search(url=source.careers_url, company=source.company)
            else:
                coverage[source_key] = {"source_status": "known_but_unavailable", "reason": "No source endpoint is configured."}
                continue
            reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
            if not reservation["reserved_calls"]:
                break
            fetch_tasks.append((source_key, source.ats_type or "public", fetch))
            sources_by_key[source_key] = source
            source_calls += 1
        remaining_budget = (
            max(0.0, JUDGE_HARD_SEARCH_SECONDS - (perf_counter() - search_started))
            if judge_mode
            else None
        )
        fetched = run_provider_fetches(fetch_tasks, timeout_seconds=remaining_budget)
        provider_timed_out = any(item[2] for item in fetched.values())
        for source_key, (result, provider_duration_ms, timed_out) in fetched.items():
            source = sources_by_key[source_key]
            telemetry.observe(
                "provider_fetch",
                provider_duration_ms,
                provider=source.ats_type or "public",
                candidate_count=len(result.records),
                success=result.ok,
                timed_out=timed_out,
            )
            if (
                result.ok
                and not result.records
                and source.careers_url
                and not (judge_mode and perf_counter() - search_started >= JUDGE_HARD_SEARCH_SECONDS)
                and os.getenv("PLAYWRIGHT_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}
            ):
                render_reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
                if render_reservation["reserved_calls"]:
                    host = urlsplit(source.careers_url).hostname
                    rendered = self.playwright.fetch(url=source.careers_url, company=source.company, allowed_hosts={host} if host else None)
                    source_calls += 1
                    if rendered.ok and rendered.records:
                        result = rendered
            if result.ok and source.careers_url and source.ats_type not in {"greenhouse", "lever"}:
                detail_records: list[dict[str, Any]] = []
                detail_content: list[str] = []
                official_host = urlsplit(source.careers_url).hostname
                for discovered in result.records:
                    if judge_mode and perf_counter() - search_started >= JUDGE_HARD_SEARCH_SECONDS:
                        warnings.append("Live provider detail budget reached; partial results were preserved.")
                        break
                    detail_url = discovered.get("application_url")
                    if not detail_url:
                        continue
                    detail_reservation = self.repository.reserve_search_source_calls(
                        user_id, session["search_session_id"], 1
                    )
                    if not detail_reservation["reserved_calls"]:
                        break
                    provider_started = perf_counter()
                    detail = self.public_pages.fetch_job_detail(
                        url=detail_url,
                        company=source.company,
                        allowed_hosts={official_host} if official_host else None,
                    )
                    telemetry.observe(
                        "provider_fetch",
                        round((perf_counter() - provider_started) * 1000),
                        provider=detail.source_name,
                        candidate_count=len(detail.records),
                        success=detail.ok,
                    )
                    source_calls += 1
                    if detail.ok and detail.records:
                        detail_records.extend(detail.records)
                        detail_content.append(detail.raw_content)
                # Listing anchors are discovery metadata and never become final jobs.
                result.records = detail_records
                result.raw_content = "\n\n".join(detail_content)
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
                first_iteration=progressed.get(source_key, {}).get("first_iteration") or iteration,
                last_iteration=iteration,
                last_success_at=datetime.now(timezone.utc) if result.ok else None,
                last_error_type=result.error_type,
            )
            if not result.ok:
                warnings.append(f"{source.company}/{result.source_name}: source unavailable")
                continue
            evidence_started = perf_counter()
            evidence, storage_warnings = self.evidence.store(
                user_id=user_id,
                run_id=run_id,
                source_type="job_source",
                source_name=f"{source.company} {result.source_name}",
                source_url=result.source_url,
                content_type=result.content_type,
                raw_content=result.raw_content,
                structured_content={"record_count": len(result.records)},
                index_for_retrieval=False,
                phase_observer=telemetry.observe,
            )
            telemetry.observe(
                "evidence_persistence",
                round((perf_counter() - evidence_started) * 1000),
                provider=result.source_name,
                candidate_count=len(result.records),
            )
            warnings.extend(storage_warnings)
            evidence_ids.append(evidence["evidence_id"])
            for item in result.records:
                item.update(source_name=result.source_name, source_url=result.source_url, evidence_ids=[evidence["evidence_id"]])
                raw_records.append(item)

        tavily_enabled = os.getenv("TAVILY_ENABLED", "false").strip().casefold() == "true" and bool(os.getenv("TAVILY_API_KEY", "").strip())
        if tavily_enabled and source_calls < max_calls and not (
            judge_mode and perf_counter() - search_started >= JUDGE_HARD_SEARCH_SECONDS
        ):
            reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
            if reservation["reserved_calls"]:
                query = " ".join([*(request.target_roles or request.role_keywords), *(request.preferred_companies or []), "jobs careers"])
                domains = []
                for source in targets:
                    candidate_url = source.official_source_url or source.careers_url
                    host = urlsplit(candidate_url).hostname if candidate_url else None
                    if host:
                        domains.append(host)
                provider_started = perf_counter()
                discovery = self.tavily.search(query=query, max_results=min(5, request.max_results), include_domains=domains or None)
                telemetry.observe(
                    "provider_discovery",
                    round((perf_counter() - provider_started) * 1000),
                    provider="tavily",
                    candidate_count=len(discovery.records),
                    success=discovery.ok,
                )
                source_calls += 1
                coverage["tavily"] = {"source_status": discovery.source_status, "returned_count": len(discovery.records), "discovery_only": True}
                for discovered in discovery.records:
                    if judge_mode and perf_counter() - search_started >= JUDGE_HARD_SEARCH_SECONDS:
                        warnings.append("Live discovery budget reached; partial results were preserved.")
                        break
                    if source_calls >= max_calls:
                        break
                    url = discovered.get("url")
                    if not url:
                        continue
                    detail_reservation = self.repository.reserve_search_source_calls(user_id, session["search_session_id"], 1)
                    if not detail_reservation["reserved_calls"]:
                        break
                    host = urlsplit(url).hostname
                    provider_started = perf_counter()
                    detail = self.public_pages.fetch_job_detail(url=url, allowed_hosts={host} if host else None)
                    telemetry.observe(
                        "provider_fetch",
                        round((perf_counter() - provider_started) * 1000),
                        provider=detail.source_name,
                        candidate_count=len(detail.records),
                        success=detail.ok,
                    )
                    source_calls += 1
                    if not detail.ok:
                        continue
                    evidence_started = perf_counter()
                    evidence, storage_warnings = self.evidence.store(
                        user_id=user_id,
                        run_id=run_id,
                        source_type="job_detail",
                        source_name=detail.source_name,
                        source_url=detail.source_url,
                        content_type=detail.content_type,
                        raw_content=detail.raw_content,
                        structured_content=detail.records[0] if detail.records else None,
                        index_for_retrieval=False,
                        phase_observer=telemetry.observe,
                    )
                    telemetry.observe(
                        "evidence_persistence",
                        round((perf_counter() - evidence_started) * 1000),
                        provider=detail.source_name,
                        candidate_count=len(detail.records),
                    )
                    warnings.extend(storage_warnings)
                    evidence_ids.append(evidence["evidence_id"])
                    for item in detail.records:
                        item.update(source_name=detail.source_name, source_url=detail.source_url, evidence_ids=[evidence["evidence_id"]])
                        raw_records.append(item)
        dedupe_started = perf_counter()
        deduplicated = deduplicate_jobs(raw_records)
        telemetry.observe(
            "deduplication",
            round((perf_counter() - dedupe_started) * 1000),
            candidate_count=len(deduplicated),
        )
        normalized_started = perf_counter()
        hard_filter_duration_seconds = 0.0
        normalized: list[JobCandidate] = list(existing_candidates)
        role_terms = [_normalized(item) for item in request.target_roles + request.role_keywords if item]
        for item in deduplicated:
            description = str(item.get("description") or "")[:20_000]
            text = _normalized(f"{item.get('title')} {description}")
            eligibility = item.get("eligibility") or extract_explicit_eligibility(description)
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
                description_excerpt=description[:1500] or None,
                salary=item.get("salary"),
                evidence_ids=item.get("evidence_ids") or [],
                eligibility_evidence_id=(item.get("evidence_ids") or [None])[0] if eligibility else None,
                source_keys=[f"{item['source_name']}:{_normalized(item.get('company'))}"],
            )
            profile_skills = {_normalized(item) for item in request.profile_skills}
            overlap = sorted(skill for skill in profile_skills if skill and skill in text)
            required_skills, preferred_skills = extract_explicit_job_skills(description, request)
            desired_overlap = sorted(skill for skill in request.desired_job_skills if _normalized(skill) in text)
            candidate.job_required_skills = required_skills
            candidate.job_preferred_skills = preferred_skills
            candidate.deterministic_match_features = {"profile_skill_overlap": overlap, "desired_skill_overlap": desired_overlap, "role_term_overlap": [term for term in role_terms if term in text]}
            candidate.fit_score = None
            candidate.transferable_skills = overlap
            candidate.skill_gaps = sorted(skill for skill in required_skills if _normalized(skill) not in profile_skills)
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
            hard_started = perf_counter()
            apply_hard_filters(candidate, request)
            hard_filter_duration_seconds += perf_counter() - hard_started
            if candidate.candidate_id not in {existing.candidate_id for existing in normalized}:
                normalized.append(candidate)

        hard_filter_duration_ms = round(hard_filter_duration_seconds * 1000)
        telemetry.observe(
            "normalization",
            max(0, round((perf_counter() - normalized_started) * 1000) - hard_filter_duration_ms),
            candidate_count=len(normalized),
        )
        telemetry.observe(
            "hard_filtering",
            hard_filter_duration_ms,
            candidate_count=len(normalized),
        )
        shortlist_started = perf_counter()
        rankable = lexical_job_shortlist(
            [item for item in normalized if item.requirement_status != RequirementStatus.CONFLICT],
            request,
            limit=30,
        )
        telemetry.observe("sparse_shortlist", round((perf_counter() - shortlist_started) * 1000), candidate_count=len(rankable), metadata={"shortlist_count": len(rankable)})
        for candidate in rankable:
            trust = assess_job_source(
                candidate.source_url,
                provider=candidate.source_name,
                approved_hosts=approved_job_hosts,
            )
            candidate.deterministic_match_features["source_trust"] = trust.model_dump()
            candidate.source_status = SourceStatus.OFFICIAL_SOURCE if trust.trusted_for_claims else SourceStatus.UNVERIFIED_PUBLIC_SOURCE
        embedding_candidates = rankable[:20]
        embedding_started = perf_counter()
        documents, embedding_stats = self.indexer.index_candidate_batch(
            corpus_type="job", user_id=user_id,
            search_session_id=session["search_session_id"], run_id=run_id,
            candidates=[{
                "candidate_id": candidate.candidate_id,
                "title": f"{candidate.title or ''} — {candidate.company or ''}".strip(" —"),
                "text": "\n".join(filter(None, [candidate.title, candidate.company, candidate.location, candidate.employment_type, candidate.description_excerpt, candidate.eligibility])),
                "metadata": {"source_name": candidate.source_name, "source_url": candidate.source_url, "source_status": candidate.source_status, "requirement_status": candidate.requirement_status, "verification_status": candidate.verification_status, "hard_requirement_states": {key: str(value) for key, value in candidate.hard_requirement_states.items()}},
                "evidence_ids": candidate.evidence_ids,
            } for candidate in embedding_candidates], max_workers=4,
        )
        telemetry.observe("embedding", round((perf_counter() - embedding_started) * 1000), candidate_count=len(embedding_candidates), embedding_count=embedding_stats["embedding_count"], embedding_cache_hit_count=embedding_stats["embedding_cache_hit_count"])
        indexed_ids = [item["retrieval_document_id"] for item in documents]
        query = " ".join([*request.target_roles, *request.role_keywords, *request.desired_job_skills, *request.industries]).strip() or "career opportunity"
        ranked_ids: list[str] = []
        rank_components: dict[str, dict[str, Any]] = {}
        if indexed_ids:
            retrieval_result = self.retrieval.retrieve(user_id=user_id, query=query, corpus_types=["job"], top_k=min(request.max_results, 10), document_ids=indexed_ids, phase_observer=telemetry.observe)
            warnings.extend(retrieval_result.warnings)
            for hit in retrieval_result.items:
                candidate_id = str(hit.metadata.get("candidate_id") or "")
                if candidate_id and candidate_id not in ranked_ids:
                    ranked_ids.append(candidate_id)
                    rank_components[candidate_id] = {key: getattr(hit, key) for key in ("sparse_rank", "dense_rank", "sparse_score", "dense_score", "rrf_score", "rerank_score", "rerank_rank")}
        order = {candidate_id: index for index, candidate_id in enumerate(ranked_ids)}
        rankable.sort(key=lambda item: (order.get(item.candidate_id, len(order)), item.candidate_id))
        for candidate in rankable:
            candidate.ranking_components = rank_components.get(candidate.candidate_id, {})
        verified = [item for item in rankable if item.verification_status == "verified"]
        unverified = [item for item in rankable if item.verification_status == "requirements_not_fully_verified"]
        requested = request.requested_count
        stop_reason = "enough_verified_candidates" if len(verified) >= requested else ("source_budget_exhausted" if source_calls >= max_calls else "sources_exhausted")
        sufficiency = SearchSufficiency(
            requested_count=requested,
            verified_count=len(verified),
            unverified_count=len(unverified),
            remaining_source_budget=max(0, max_calls - source_calls),
            new_verified_candidates_this_iteration=len(verified),
            can_refine=len(verified) < requested and int(session.get("remaining_source_budget") or max_calls) > source_calls,
            stop_reason=stop_reason,
            limiting_constraints=sorted({field for item in unverified for field in item.unknown_fields}),
            suggested_relaxations=[],
        )
        live_records = [
            item.model_dump(mode="json") for item in [*verified, *unverified]
        ][:10]
        demo_records: list[dict[str, Any]] = []
        if should_use_demo_fallback(
            judge_mode=judge_mode,
            useful_live_count=len(live_records),
            elapsed_seconds=perf_counter() - search_started,
            provider_timed_out=provider_timed_out,
        ):
            live_ids = {item["candidate_id"] for item in live_records}
            demo_records = [
                JobCandidate.model_validate(item).model_dump(mode="json")
                for item in load_demo_search_fixtures("jobs")
                if item.get("candidate_id") not in live_ids
            ][: max(0, min(request.requested_count, 10) - len(live_records))]
            if demo_records:
                warnings.append(
                    "Demo snapshot suggestions are historical public-source samples, "
                    "not claims that a posting is currently open."
                )
                telemetry.observe(
                    "demo_snapshot_fallback",
                    0,
                    candidate_count=len(demo_records),
                    metadata={"display_count": len(demo_records)},
                )
        records = [*live_records, *demo_records][:10]
        session = self.repository.update_search_session(
            user_id,
            session["search_session_id"],
            iteration=iteration,
            visited_sources=list(dict.fromkeys([*(session.get("visited_sources") or []), *coverage])),
            seen_candidate_ids=[item["candidate_id"] for item in records],
            candidate_records=records,
            source_coverage=coverage,
            consecutive_no_progress=0 if len(records) > len(cached) else int(session.get("consecutive_no_progress") or 0) + 1,
            status="active",
        )
        result = self._page_result(records, request, session, offset=offset)
        result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
        result.evidence_ids = list(dict.fromkeys([*result.evidence_ids, *evidence_ids]))
        result.source_calls = source_calls
        if isinstance(result.data, dict):
            result.data["sufficiency"] = sufficiency.model_dump(mode="json")
        telemetry.observe(
            "search_tool",
            round((perf_counter() - search_started) * 1000),
            candidate_count=len(result.data["page"]["items"]),
        )
        return result

    @staticmethod
    def _page_result(
        records: list[dict[str, Any]],
        request: JobSearchRequest,
        session: dict[str, Any],
        *,
        offset: int,
    ) -> ToolExecutionResult:
        page_size = min(request.page_size, 10)
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
                    "last_seen_iteration", "source_keys", "hard_requirement_states",
                    "job_required_skills", "job_preferred_skills", "ranking_components",
                    "verification_status",
                    "source_status", "requirement_status", "is_demo_sample",
                    "snapshot_date", "source_verified_at_snapshot",
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
