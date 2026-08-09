from __future__ import annotations

import os
from typing import Any

from sqlalchemy import inspect, text

from app.database.database import create_database_engine


class CockroachDiagnostics:
    """Whitelisted read-only diagnostics, separate from Career Agent tools."""

    def __init__(self, engine=None):
        self.enabled = os.getenv("COCKROACH_MCP_ENABLED", "false").casefold() == "true"
        self.engine = engine or create_database_engine()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("Cockroach diagnostics are disabled.")

    def list_tables(self) -> list[str]:
        self._require_enabled()
        return sorted(inspect(self.engine).get_table_names())

    def table_schema(self, table_name: str) -> list[dict[str, Any]]:
        self._require_enabled()
        if table_name not in set(inspect(self.engine).get_table_names()):
            raise ValueError("Unknown table.")
        return [{"name": item["name"], "type": str(item["type"]), "nullable": item["nullable"]} for item in inspect(self.engine).get_columns(table_name)]

    def retrieval_counts(self) -> list[dict[str, Any]]:
        self._require_enabled()
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT corpus_type, count(*) AS count FROM retrieval_documents GROUP BY corpus_type ORDER BY corpus_type")).mappings()
            return [dict(row) for row in rows]

    def explain_retrieval_shape(self) -> list[str]:
        self._require_enabled()
        with self.engine.connect() as connection:
            rows = connection.execute(text("EXPLAIN SELECT retrieval_document_id FROM retrieval_documents WHERE user_id IS NULL LIMIT 10"))
            return [str(row[0]) for row in rows]
