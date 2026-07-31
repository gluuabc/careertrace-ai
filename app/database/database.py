import os
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
    """Create prototype tables. Alembic migrations can replace this later."""

    from app.database import models  # noqa: F401

    Base.metadata.create_all(bind=target_engine or engine)


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
