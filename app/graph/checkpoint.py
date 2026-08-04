import atexit
import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.database.database import PROJECT_ROOT

DEFAULT_CHECKPOINT_PATH = PROJECT_ROOT / "data" / "langgraph_checkpoints.sqlite"
_default_saver: SqliteSaver | None = None


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


def get_default_checkpointer() -> SqliteSaver:
    """Return the process-wide durable checkpointer used by Streamlit."""

    global _default_saver
    if _default_saver is None:
        _default_saver = create_sqlite_checkpointer()
    return _default_saver


def _close_default_checkpointer() -> None:
    global _default_saver
    if _default_saver is not None:
        _default_saver.conn.close()
        _default_saver = None


atexit.register(_close_default_checkpointer)
