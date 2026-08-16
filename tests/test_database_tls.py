from __future__ import annotations

import stat
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.database import database as database_module
from app.graph import checkpoint as checkpoint_module


SYNTHETIC_CA = """-----BEGIN CERTIFICATE-----
SYNTHETIC_TEST_CA_MATERIAL
-----END CERTIFICATE-----"""


def test_ca_secret_is_materialized_with_restrictive_permissions(tmp_path):
    destination = tmp_path / "cockroach-ca.crt"

    result = database_module.materialize_cockroach_ca(SYNTHETIC_CA, destination)

    assert result == destination
    assert destination.read_text() == SYNTHETIC_CA + "\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_ca_secret_preserves_query_and_overrides_sslrootcert(tmp_path, monkeypatch):
    destination = tmp_path / "runtime-ca.crt"
    monkeypatch.setattr(database_module, "COCKROACH_CA_PATH", destination)
    monkeypatch.setenv("COCKROACH_CA_CERT", SYNTHETIC_CA)
    raw_url = (
        "cockroachdb://careertrace:p%40ss%2Fword@example.test:26257/careertrace"
        "?sslmode=verify-full&application_name=CareerTrace"
        "&sslrootcert=%2FUsers%2Fdeveloper%2Froot.crt"
    )

    resolved = make_url(database_module.resolve_database_url(raw_url))

    assert resolved.password == "p@ss/word"
    assert resolved.query["sslmode"] == "verify-full"
    assert resolved.query["application_name"] == "CareerTrace"
    assert resolved.query["sslrootcert"] == str(destination)
    assert "SYNTHETIC_TEST_CA_MATERIAL" not in resolved.render_as_string(
        hide_password=False
    )


def test_absent_ca_secret_does_not_materialize_or_change_local_url(
    tmp_path, monkeypatch
):
    destination = tmp_path / "must-not-exist.crt"
    monkeypatch.setattr(database_module, "COCKROACH_CA_PATH", destination)
    monkeypatch.delenv("COCKROACH_CA_CERT", raising=False)
    raw_url = (
        "cockroachdb://user:password@example.test/db"
        "?sslmode=verify-full&sslrootcert=/existing/root.crt"
    )

    assert database_module.resolve_database_url(raw_url) == raw_url
    assert not destination.exists()


def test_certificate_contents_do_not_leak_in_url_logs_or_validation_error(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(
        database_module, "COCKROACH_CA_PATH", tmp_path / "runtime-ca.crt"
    )
    monkeypatch.setenv("COCKROACH_CA_CERT", SYNTHETIC_CA)

    resolved = database_module.resolve_database_url(
        "cockroachdb://user:password@example.test/db?sslmode=verify-full"
    )

    assert SYNTHETIC_CA not in resolved
    assert SYNTHETIC_CA not in caplog.text
    with pytest.raises(ValueError) as error:
        database_module.materialize_cockroach_ca(
            "PRIVATE_INVALID_CA_CONTENT", tmp_path / "invalid.crt"
        )
    assert "PRIVATE_INVALID_CA_CONTENT" not in str(error.value)


def test_sqlalchemy_and_cockroach_checkpoint_urls_share_resolved_tls(
    tmp_path, monkeypatch
):
    destination = tmp_path / "shared-ca.crt"
    monkeypatch.setattr(database_module, "COCKROACH_CA_PATH", destination)
    monkeypatch.setenv("COCKROACH_CA_CERT", SYNTHETIC_CA)
    captured: dict[str, str] = {}

    def capture_engine(url: str, **_options):
        captured["application"] = url
        return object()

    monkeypatch.setattr(database_module, "create_engine", capture_engine)
    raw_url = (
        "cockroachdb://user:p%40ss@example.test/db"
        "?sslmode=verify-full&application_name=CareerTrace"
    )

    database_module.create_database_engine(raw_url)
    checkpoint_resolved = checkpoint_module.resolve_database_url(raw_url)
    checkpoint_url = checkpoint_module._cockroach_connection_string(
        checkpoint_resolved, "careertrace_checkpoints"
    )
    application = make_url(captured["application"])
    checkpoint = make_url(checkpoint_url)

    assert (
        checkpoint_module.resolve_database_url
        is database_module.resolve_database_url
    )
    assert application.password == checkpoint.password == "p@ss"
    assert (
        application.query["sslmode"]
        == checkpoint.query["sslmode"]
        == "verify-full"
    )
    assert (
        application.query["sslrootcert"]
        == checkpoint.query["sslrootcert"]
        == str(destination)
    )
    assert (
        application.query["application_name"]
        == checkpoint.query["application_name"]
        == "CareerTrace"
    )
    assert checkpoint.query["options"] == "-csearch_path=careertrace_checkpoints"
