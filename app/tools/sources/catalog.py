from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from app.database.database import PROJECT_ROOT


@dataclass(frozen=True)
class CompanySource:
    company: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    ats_type: str | None = None
    board_token: str | None = None
    lever_site: str | None = None
    careers_url: str | None = None
    student_careers_url: str | None = None
    official_source_url: str | None = None
    enabled: bool = False
    last_verified_at: date | None = None
    verification_status: str = "unverified"


class CompanyCatalog:
    def __init__(self, path: Path | None = None):
        self.path = path or PROJECT_ROOT / "config" / "job_sources.yaml"
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.sources = tuple(self._parse(item) for item in raw.get("companies", []))

    @staticmethod
    def _parse(item: dict) -> CompanySource:
        verified = item.get("last_verified_at")
        return CompanySource(
            company=str(item["company"]),
            aliases=tuple(str(value) for value in item.get("aliases") or []),
            ats_type=item.get("ats_type"),
            board_token=item.get("board_token"),
            lever_site=item.get("lever_site"),
            careers_url=item.get("careers_url"),
            student_careers_url=item.get("student_careers_url"),
            official_source_url=item.get("official_source_url"),
            enabled=bool(item.get("enabled", False)),
            last_verified_at=date.fromisoformat(str(verified)) if verified else None,
            verification_status=str(item.get("verification_status") or "unverified"),
        )

    def find(self, company: str) -> CompanySource | None:
        key = company.strip().casefold()
        return next(
            (
                source
                for source in self.sources
                if key in {source.company.casefold(), *(item.casefold() for item in source.aliases)}
            ),
            None,
        )

    def enabled(self) -> list[CompanySource]:
        return [item for item in self.sources if item.enabled and item.verification_status == "verified"]
