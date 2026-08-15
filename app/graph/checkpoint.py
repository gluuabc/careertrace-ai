import atexit
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy.engine import make_url

from app.database.database import PROJECT_ROOT, resolve_database_url

DEFAULT_CHECKPOINT_PATH = PROJECT_ROOT / "data" / "langgraph_checkpoints.sqlite"
_default_saver: Any | None = None
_default_saver_context: Any | None = None


def resolve_checkpoint_path(path: str | Path | None = None) -> Path:
    """Resolve workflow storage independently from the launch directory."""

    configured = path or os.getenv(
        "LANGGRAPH_CHECKPOINT_DB", str(DEFAULT_CHECKPOINT_PATH)
    )
    resolved = Path(configured).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    resolved = resolved.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def create_sqlite_checkpointer(
    path: str | Path | None = None,
) -> SqliteSaver:
    """Create a durable workflow checkpointer; callers own its connection."""

    connection = sqlite3.connect(
        resolve_checkpoint_path(path), check_same_thread=False
    )
    return SqliteSaver(connection)


def checkpoint_backend() -> str:
    """Return the explicit persistence backend; never infer from a hostname."""

    backend = os.getenv("LANGGRAPH_CHECKPOINT_BACKEND", "sqlite").strip().casefold()
    if backend not in {"sqlite", "cockroachdb"}:
        raise ValueError(
            "LANGGRAPH_CHECKPOINT_BACKEND must be sqlite or cockroachdb."
        )
    return backend


def _cockroach_connection_string(database_url: str, schema: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("LANGGRAPH_CHECKPOINT_SCHEMA is invalid.")
    parsed = make_url(database_url)
    if not parsed.drivername.startswith("cockroachdb"):
        raise ValueError(
            "Cockroach checkpoint mode requires a cockroachdb DATABASE_URL."
        )
    return (
        parsed.set(drivername="postgresql")
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )


def create_cockroach_checkpointer(
    database_url: str | None = None,
    schema: str | None = None,
):
    """Create the official Cockroach saver in its own application schema."""

    from langchain_cockroachdb import CockroachDBSaver
    from psycopg import Connection
    from psycopg import sql

    raw_url = resolve_database_url(database_url)
    checkpoint_schema = (
        schema or os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "careertrace_checkpoints")
    ).strip()
    connection_string = _cockroach_connection_string(raw_url, checkpoint_schema)
    # Schema creation is the only setup outside the official saver. All checkpoint
    # tables, migrations, reads, and writes remain owned by CockroachDBSaver.
    base_string = (
        make_url(raw_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    with Connection.connect(base_string, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(checkpoint_schema)
            )
        )
    context = CockroachDBSaver.from_conn_string(connection_string)
    saver = context.__enter__()
    try:
        saver.setup()
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    return saver, context


def get_default_checkpointer():
    """Return the configured process-wide durable Streamlit checkpointer."""

    global _default_saver, _default_saver_context
    if _default_saver is None:
        if checkpoint_backend() == "cockroachdb":
            _default_saver, _default_saver_context = create_cockroach_checkpointer()
        else:
            _default_saver = create_sqlite_checkpointer()
    return _default_saver


def _close_default_checkpointer() -> None:
    global _default_saver, _default_saver_context
    if _default_saver is not None:
        if _default_saver_context is not None:
            _default_saver_context.__exit__(None, None, None)
        else:
            _default_saver.conn.close()
        _default_saver = None
        _default_saver_context = None


atexit.register(_close_default_checkpointer)
