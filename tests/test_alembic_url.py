from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.database import database as database_module


SYNTHETIC_CA = """-----BEGIN CERTIFICATE-----
SYNTHETIC_ALEMBIC_TEST_CA
-----END CERTIFICATE-----"""


def test_alembic_config_round_trips_encoded_cockroach_url(tmp_path, monkeypatch, caplog):
    certificate_path = tmp_path / "cockroach-ca.crt"
    monkeypatch.setattr(database_module, "COCKROACH_CA_PATH", certificate_path)
    monkeypatch.setenv("COCKROACH_CA_CERT", SYNTHETIC_CA)
    raw_url = (
        "cockroachdb://careertrace:p%40ss%25word@example.test:26257/careertrace"
        "?sslmode=verify-full&application_name=Career%20Trace"
    )
    resolved_url = database_module.resolve_database_url(raw_url)
    config = Config()

    config.set_main_option(
        "sqlalchemy.url",
        database_module.escape_alembic_config_value(resolved_url),
    )

    effective_url = config.get_main_option("sqlalchemy.url")
    parsed = make_url(effective_url)
    assert effective_url == resolved_url
    assert parsed.password == "p@ss%word"
    assert parsed.query["sslmode"] == "verify-full"
    assert parsed.query["application_name"] == "Career Trace"
    assert parsed.query["sslrootcert"] == str(certificate_path)
    assert "p@ss%word" not in caplog.text
    assert "p%40ss%25word" not in caplog.text
    assert SYNTHETIC_CA not in caplog.text


def test_init_db_uses_escaped_alembic_boundary(tmp_path, monkeypatch):
    certificate_path = tmp_path / "cockroach-ca.crt"
    monkeypatch.setattr(database_module, "COCKROACH_CA_PATH", certificate_path)
    monkeypatch.setenv("COCKROACH_CA_CERT", SYNTHETIC_CA)
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "cockroachdb://careertrace:p%40ss%25word@example.test:26257/careertrace"
            "?sslmode=verify-full&application_name=Career%20Trace"
        ),
    )
    captured: dict[str, Config] = {}

    def capture_upgrade(config: Config, revision: str):
        captured["config"] = config
        assert revision == "head"

    monkeypatch.setattr(command, "upgrade", capture_upgrade)

    database_module.init_db()

    effective_url = captured["config"].get_main_option("sqlalchemy.url")
    assert effective_url == database_module.resolve_database_url()
    parsed = make_url(effective_url)
    assert parsed.query["sslmode"] == "verify-full"
    assert parsed.query["sslrootcert"] == str(certificate_path)


def test_sqlite_alembic_config_behavior_is_unchanged():
    resolved_url = database_module.resolve_database_url("sqlite:///:memory:")
    config = Config()

    config.set_main_option(
        "sqlalchemy.url",
        database_module.escape_alembic_config_value(resolved_url),
    )

    assert resolved_url == "sqlite:///:memory:"
    assert config.get_main_option("sqlalchemy.url") == resolved_url
