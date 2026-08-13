from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceTrustDecision:
    trusted_for_discovery: bool
    trusted_for_claims: bool
    trust_reason: str

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def _host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").casefold().rstrip(".")


def assess_job_source(
    url: str | None,
    *,
    approved_hosts: set[str] | None = None,
    provider: str | None = None,
) -> SourceTrustDecision:
    host = _host(url)
    allowed = {item.casefold().rstrip(".") for item in approved_hosts or set()}
    if provider in {"greenhouse", "lever"} and host in {
        "boards-api.greenhouse.io",
        "api.lever.co",
    }:
        return SourceTrustDecision(True, True, "verified_company_specific_ats")
    if host and any(host == item or host.endswith(f".{item}") for item in allowed):
        return SourceTrustDecision(True, True, "verified_company_owned_domain")
    if host:
        return SourceTrustDecision(True, False, "safe_but_not_authoritative")
    return SourceTrustDecision(False, False, "missing_public_source")


def assess_people_source(
    url: str | None,
    *,
    provider: str | None = None,
    approved_hosts: set[str] | None = None,
) -> SourceTrustDecision:
    host = _host(url)
    if provider == "openalex" and host == "openalex.org":
        return SourceTrustDecision(True, True, "approved_academic_metadata")
    if provider == "wikidata" and host in {"wikidata.org", "www.wikidata.org"}:
        return SourceTrustDecision(True, True, "approved_structured_identity_metadata")
    allowed = {item.casefold().rstrip(".") for item in approved_hosts or set()}
    institutional = host.endswith(".edu") or host.endswith(".ac.uk") or any(
        host == item or host.endswith(f".{item}") for item in allowed
    )
    if host and institutional:
        return SourceTrustDecision(True, True, "approved_public_institutional_domain")
    if host:
        return SourceTrustDecision(True, False, "safe_but_not_authoritative")
    return SourceTrustDecision(False, False, "missing_public_source")
