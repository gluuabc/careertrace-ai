import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = f"sqlite:///{PROJECT_ROOT / 'data' / 'careertrace.db'}"
_migration_lock = threading.Lock()


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy models."""


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a portable engine with SQLite behavior isolated here."""

    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
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
            "sqlalchemy.url", os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
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
