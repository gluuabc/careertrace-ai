import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterator, TypeVar

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = f"sqlite:///{PROJECT_ROOT / 'data' / 'careertrace.db'}"
COCKROACH_CA_PATH = Path(tempfile.gettempdir()) / "careertrace-cockroach-ca.crt"
_migration_lock = threading.Lock()
_ca_materialization_lock = threading.Lock()
T = TypeVar("T")


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy models."""


def materialize_cockroach_ca(
    certificate: str,
    destination: str | Path | None = None,
) -> Path:
    """Write a configured Cockroach CA PEM to a private runtime file."""

    pem = certificate.strip()
    if not (
        pem.startswith("-----BEGIN CERTIFICATE-----")
        and "-----END CERTIFICATE-----" in pem
    ):
        raise ValueError("COCKROACH_CA_CERT must contain a PEM certificate.")

    target = Path(destination or COCKROACH_CA_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _ca_materialization_lock:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(pem + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            target.chmod(0o600)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
    return target


def resolve_database_url(database_url: str | None = None) -> str:
    """Resolve portable SQLite paths and optional Cockroach CA configuration."""

    raw_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    parsed = make_url(raw_url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in {
        None,
        "",
        ":memory:",
    }:
        database_path = Path(parsed.database).expanduser()
        if not database_path.is_absolute():
            database_path = (PROJECT_ROOT / database_path).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        return str(parsed.set(database=str(database_path)))
    if parsed.drivername.startswith("cockroachdb"):
        certificate = os.getenv("COCKROACH_CA_CERT", "").strip()
        if certificate:
            certificate_path = materialize_cockroach_ca(certificate)
            return parsed.update_query_dict(
                {"sslrootcert": str(certificate_path)}, append=False
            ).render_as_string(hide_password=False)
    return raw_url


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a portable engine with SQLite behavior isolated here."""

    url = resolve_database_url(database_url)
    options: dict = {"future": True}

    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            options["poolclass"] = StaticPool

    engine = create_engine(url, **options)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


engine = create_database_engine()
SessionLocal = create_session_factory(engine)


def init_db(target_engine: Engine | None = None) -> None:
    """Create test schemas directly or migrate the configured runtime database."""

    from app.database import models  # noqa: F401

    if target_engine is not None:
        Base.metadata.create_all(bind=target_engine)
        return

    from alembic import command
    from alembic.config import Config

    with _migration_lock:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        config.set_main_option(
            "sqlalchemy.url", resolve_database_url()
        )
        command.upgrade(config, "head")


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> Iterator[Session]:
    """Provide a transaction boundary for deterministic memory operations."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_retryable_transaction(
    callback: Callable[[Session], T],
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    max_retries: int = 3,
    max_backoff: int = 1,
    _runner: Callable[..., Any] | None = None,
) -> T:
    """Run one short, database-only unit with bounded Cockroach retries.

    ``callback`` may be invoked more than once on CockroachDB. It must contain
    only idempotent database work on the supplied session: never provider,
    embedding, HTTP, browser, or other external side effects.
    """

    retries = max(0, min(int(max_retries), 10))
    backoff = max(0, min(int(max_backoff), 10))
    bind = session_factory.kw.get("bind")
    if bind is not None and bind.dialect.name == "cockroachdb":
        if _runner is None:
            from sqlalchemy_cockroachdb import run_transaction

            _runner = run_transaction
        return _runner(
            session_factory,
            callback,
            max_retries=retries,
            max_backoff=backoff,
        )

    with session_scope(session_factory) as session:
        return callback(session)
