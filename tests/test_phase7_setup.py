from __future__ import annotations

import re
import tomllib
from pathlib import Path

from scripts.check_setup import configuration_checks, run_setup_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _valid_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "sqlite:///data/test.db",
        "LANGGRAPH_CHECKPOINT_BACKEND": "sqlite",
        "AWS_REGION": "us-east-1",
        "BEDROCK_MODEL_CHEAP": "amazon.nova-lite-v1:0",
        "BEDROCK_MODEL_REASONING": "global.anthropic.claude-sonnet-4-6",
        "BEDROCK_COUNT_TOKENS_MODEL": "anthropic.claude-sonnet-4-20250514-v1:0",
        "BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
        "BEDROCK_EMBEDDING_DIMENSIONS": "1024",
        "S3_BUCKET_NAME": "synthetic-test-bucket",
        "S3_REGION": "us-east-1",
        "JUDGE_DEMO_ENABLED": "true",
        "JUDGE_DEMO_ACCESS_CODE": "synthetic-test-access-code",
    }


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    return values


def test_env_example_covers_runtime_settings():
    example = _env_example()
    source = "\n".join(
        path.read_text(errors="ignore")
        for root in (PROJECT_ROOT / "app", PROJECT_ROOT / "migrations", PROJECT_ROOT / "scripts")
        for path in root.rglob("*.py")
    )
    runtime_names = set(
        re.findall(r"(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']", source)
    )
    # Live test-only URLs are documented because they are part of the supported
    # verification contract even though application runtime does not read them.
    runtime_names.add("COCKROACH_TEST_DATABASE_URL")
    assert runtime_names <= set(example), sorted(runtime_names - set(example))
    assert example["JOB_SEARCH_MAX_RESULTS"] == "10"
    assert example["MEMORY_EXTRACTION_MAX_INPUT_TOKENS"] == "6000"
    assert example["BEDROCK_MODEL_REASONING"] == "global.anthropic.claude-sonnet-4-6"


def test_no_secret_values_exist_in_public_configuration():
    example = _env_example()
    secret_names = {
        "LANGSMITH_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "AUTH_COOKIE_SECRET",
        "JUDGE_DEMO_ACCESS_CODE",
        "TAVILY_API_KEY",
        "COCKROACH_CLOUD_MCP_API_KEY",
        "COCKROACH_TEST_DATABASE_URL",
        "COCKROACH_CA_CERT",
    }
    assert all(example[name] == "" for name in secret_names)
    assert not (PROJECT_ROOT / ".env").read_text(errors="ignore") in (
        PROJECT_ROOT / ".env.example"
    ).read_text(errors="ignore")


def test_setup_checker_succeeds_with_valid_required_configuration():
    checks = run_setup_checks(env=_valid_environment(), live=False)
    assert not [check for check in checks if check.failed]


def test_setup_checker_fails_cleanly_when_required_setting_missing():
    environment = _valid_environment()
    environment.pop("S3_BUCKET_NAME")
    checks = configuration_checks(environment)
    failures = [check for check in checks if check.failed]
    assert failures
    assert "S3_BUCKET_NAME" in failures[0].detail
    assert not any(environment.get(name, "") in check.detail for name in ("JUDGE_DEMO_ACCESS_CODE",) for check in failures)


def test_deployed_configuration_requires_cockroach_for_data_and_checkpoints():
    checks = configuration_checks(_valid_environment(), mode="deployed")
    failed_names = {check.name for check in checks if check.failed}
    assert failed_names == {"application database", "checkpoint backend"}


def test_deployed_cockroach_requires_verify_full_and_portable_ca():
    environment = _valid_environment()
    environment.update(
        {
            "DATABASE_URL": (
                "cockroachdb://user:password@example.test/db?sslmode=verify-full"
            ),
            "LANGGRAPH_CHECKPOINT_BACKEND": "cockroachdb",
        }
    )
    checks_without_ca = configuration_checks(environment, mode="deployed")
    assert "Cockroach TLS" in {
        check.name for check in checks_without_ca if check.failed
    }

    environment["COCKROACH_CA_CERT"] = "synthetic PEM configured"
    checks_with_ca = configuration_checks(environment, mode="deployed")
    assert not [check for check in checks_with_ca if check.failed]
    assert not any(
        environment["COCKROACH_CA_CERT"] in check.detail for check in checks_with_ca
    )


def test_checkpoint_dependency_is_cockroach_specific_not_generic_postgres():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text()
    assert "langchain-cockroachdb==0.2.1" in requirements
    assert "langgraph-checkpoint-postgres" not in requirements
    evidence = (PROJECT_ROOT / "docs" / "CHECKPOINT_COMPATIBILITY.md").read_text()
    assert "jsonb_each_text" in evidence


def test_dockerfile_has_reproducible_streamlit_start_and_no_secret_copy():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
    assert "python:3.13-slim" in dockerfile
    assert "--server.address=0.0.0.0" in dockerfile
    assert "--server.port=8501" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert ".env" in dockerignore
    assert ".streamlit/secrets.toml" in dockerignore


def test_streamlit_cloud_template_uses_deployed_persistence_without_test_secrets():
    template_path = PROJECT_ROOT / "docs" / "streamlit-secrets.example.toml"
    template = tomllib.loads(template_path.read_text())
    assert template["DATABASE_URL"].startswith("cockroachdb://")
    assert template["LANGGRAPH_CHECKPOINT_BACKEND"] == "cockroachdb"
    assert "-----BEGIN CERTIFICATE-----" in template["COCKROACH_CA_CERT"]
    assert template["BEDROCK_MODEL_REASONING"] == "global.anthropic.claude-sonnet-4-6"
    assert template["JUDGE_DEMO_ENABLED"] == "true"
    assert template["PLAYWRIGHT_ENABLED"] == "false"
    assert "COCKROACH_TEST_DATABASE_URL" not in template
    assert "LANGGRAPH_CHECKPOINT_DB" not in template
    assert not any("/Users/" in str(value) for value in template.values())
