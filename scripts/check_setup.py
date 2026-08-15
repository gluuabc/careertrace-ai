#!/usr/bin/env python3
"""CareerTrace setup doctor.

The checker prints only check names, status, and sanitized summaries. It never
prints credentials, connection URLs, provider response bodies, or user data.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    required: bool = True

    @property
    def failed(self) -> bool:
        return self.required and self.status == "FAIL"


REQUIRED_SETTINGS = (
    "DATABASE_URL",
    "LANGGRAPH_CHECKPOINT_BACKEND",
    "AWS_REGION",
    "BEDROCK_MODEL_CHEAP",
    "BEDROCK_MODEL_REASONING",
    "BEDROCK_COUNT_TOKENS_MODEL",
    "BEDROCK_EMBEDDING_MODEL",
    "BEDROCK_EMBEDDING_DIMENSIONS",
    "S3_BUCKET_NAME",
    "S3_REGION",
)


def _enabled(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def configuration_checks(
    env: Mapping[str, str], *, mode: str = "local"
) -> list[Check]:
    """Validate the public configuration contract without making connections."""

    checks: list[Check] = []
    missing = [name for name in REQUIRED_SETTINGS if not env.get(name, "").strip()]
    checks.append(
        Check(
            "required configuration",
            "FAIL" if missing else "PASS",
            "missing: " + ", ".join(missing) if missing else "all required settings are present",
        )
    )

    database_url = env.get("DATABASE_URL", "").strip().casefold()
    backend = env.get("LANGGRAPH_CHECKPOINT_BACKEND", "").strip().casefold()
    if backend not in {"sqlite", "cockroachdb"}:
        checks.append(Check("checkpoint backend", "FAIL", "must be sqlite or cockroachdb"))
    elif mode == "deployed" and backend != "cockroachdb":
        checks.append(Check("checkpoint backend", "FAIL", "deployed mode requires cockroachdb"))
    else:
        checks.append(Check("checkpoint backend", "PASS", backend))

    if mode == "deployed" and not database_url.startswith("cockroachdb"):
        checks.append(Check("application database", "FAIL", "deployed mode requires CockroachDB"))
    elif database_url:
        scheme = database_url.split(":", 1)[0]
        checks.append(Check("application database", "PASS", f"configured scheme: {scheme}"))

    google = all(
        env.get(name, "").strip()
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "AUTH_COOKIE_SECRET")
    )
    judge = _enabled(env, "JUDGE_DEMO_ENABLED") and bool(
        env.get("JUDGE_DEMO_ACCESS_CODE", "").strip()
    )
    checks.append(
        Check(
            "authentication path",
            "PASS" if google or judge else "FAIL",
            "Google OIDC and/or Judge access is configured"
            if google or judge
            else "configure Google OIDC or an enabled Judge access code",
        )
    )
    return checks


def _safe_live_check(name: str, callback, *, required: bool = True) -> Check:
    try:
        detail = callback()
        return Check(name, "PASS", str(detail or "available"), required)
    except Exception as error:  # provider errors are intentionally sanitized
        status = "FAIL" if required else "OPTIONAL/UNAVAILABLE"
        return Check(name, status, type(error).__name__, required)


def live_checks(env: Mapping[str, str], *, mode: str = "local") -> list[Check]:
    """Run bounded, synthetic connection checks against configured services."""

    import boto3
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from langchain_core.messages import HumanMessage
    from sqlalchemy import inspect, text

    from app.database.database import create_database_engine
    from app.llm.model import get_llm, resolve_bedrock_model_id
    from app.services.embeddings import TitanEmbeddingProvider
    from app.services.token_accounting import BedrockTokenAccounting
    from app.storage.s3 import S3ObjectStorage
    from app.tools.sources.tavily import TavilyAdapter

    checks: list[Check] = []

    def database() -> str:
        engine = create_database_engine(env["DATABASE_URL"])
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                context = inspect(connection)
                if "alembic_version" not in context.get_table_names():
                    raise RuntimeError("migration state is unavailable")
                current = connection.scalar(text("SELECT version_num FROM alembic_version"))
            config = Config(str(PROJECT_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
            head = ScriptDirectory.from_config(config).get_current_head()
            if current != head:
                raise RuntimeError("database migrations are not at head")
            dialect = "cockroachdb" if engine.dialect.name == "cockroachdb" else "sqlite"
            return f"connected; migrations at head; dialect={dialect}"
        finally:
            engine.dispose()

    checks.append(_safe_live_check("database and migration state", database))
    checks.append(
        _safe_live_check(
            "AWS identity",
            lambda: "credential chain resolved"
            if boto3.client("sts", region_name=env["AWS_REGION"]).get_caller_identity().get("Account")
            else (_ for _ in ()).throw(RuntimeError("identity unavailable")),
        )
    )

    def model(kind: str) -> str:
        response = get_llm(kind).invoke([HumanMessage(content="Reply with exactly OK.")])
        if not str(response.content).strip():
            raise RuntimeError("empty model response")
        configured = env[f"BEDROCK_MODEL_{kind.upper()}"]
        resolved = resolve_bedrock_model_id(configured, env["AWS_REGION"])
        return f"synthetic invocation succeeded; model={resolved}"

    checks.append(_safe_live_check("Bedrock cheap model", lambda: model("cheap")))
    checks.append(_safe_live_check("Bedrock reasoning model", lambda: model("reasoning")))

    def token_count() -> str:
        model_id = resolve_bedrock_model_id(env["BEDROCK_MODEL_REASONING"], env["AWS_REGION"])
        result = BedrockTokenAccounting(region=env["AWS_REGION"]).count_message_input(
            model_id,
            [HumanMessage(content="CareerTrace setup check")],
            tools=[],
            exact_trigger=0,
        )
        if result.count_source != "bedrock_count_tokens":
            raise RuntimeError("Bedrock CountTokens did not succeed")
        return "provider CountTokens succeeded using configured token-count model"

    checks.append(_safe_live_check("Bedrock CountTokens", token_count))

    def embedding() -> str:
        provider = TitanEmbeddingProvider()
        vector = provider.embed("Synthetic CareerTrace setup check")
        return f"Titan embedding succeeded; dimensions={len(vector)}"

    checks.append(_safe_live_check("Titan embedding", embedding))

    def s3_roundtrip() -> str:
        storage = S3ObjectStorage()
        key = f"setup-doctor/{uuid4()}.txt"
        payload = b"CareerTrace synthetic setup check"
        try:
            storage.put(key, payload, "text/plain")
            if storage.get(key) != payload:
                raise RuntimeError("S3 round trip mismatch")
        finally:
            storage.delete(key)
        return "put/get/delete succeeded"

    checks.append(_safe_live_check("S3 read/write/delete", s3_roundtrip))

    if _enabled(env, "TAVILY_ENABLED"):
        def tavily() -> str:
            result = TavilyAdapter().search(query="site:example.com software engineering", max_results=1)
            if not result.ok or result.skipped:
                raise RuntimeError("Tavily request failed")
            return "bounded discovery request succeeded"
        checks.append(_safe_live_check("Tavily", tavily))
    else:
        checks.append(Check("Tavily", "OPTIONAL/DISABLED", "disabled", False))

    if _enabled(env, "PLAYWRIGHT_ENABLED"):
        def playwright() -> str:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content("<title>CareerTrace setup check</title>")
                if page.title() != "CareerTrace setup check":
                    raise RuntimeError("browser smoke failed")
                browser.close()
            return "Chromium launch succeeded"
        checks.append(_safe_live_check("Playwright", playwright))
    else:
        checks.append(Check("Playwright", "OPTIONAL/DISABLED", "disabled", False))

    checks.append(
        Check(
            "Cockroach Cloud MCP",
            "DEVELOPER ONLY",
            "configured" if _enabled(env, "COCKROACH_CLOUD_MCP_ENABLED") else "optional/disabled",
            False,
        )
    )
    return checks


def run_setup_checks(
    *, env: Mapping[str, str] | None = None, mode: str = "local", live: bool = True
) -> list[Check]:
    environment = os.environ if env is None else env
    checks = [
        Check(
            "Python version",
            "PASS" if (3, 12) <= sys.version_info[:2] < (3, 14) else "FAIL",
            f"{sys.version_info.major}.{sys.version_info.minor}; supported 3.12-3.13",
        )
    ]
    checks.extend(configuration_checks(environment, mode=mode))
    if live and not any(item.failed for item in checks):
        checks.extend(live_checks(environment, mode=mode))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CareerTrace setup safely.")
    parser.add_argument("--mode", choices=("local", "deployed"), default="local")
    parser.add_argument(
        "--configuration-only",
        action="store_true",
        help="Validate configuration without making service connections.",
    )
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    checks = run_setup_checks(mode=args.mode, live=not args.configuration_only)
    for item in checks:
        print(f"{item.status:20} {item.name}: {item.detail}")
    return 1 if any(item.failed for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
