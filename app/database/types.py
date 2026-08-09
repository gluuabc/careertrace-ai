from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator, UserDefinedType


class _CockroachVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def get_col_spec(self, **_kw) -> str:
        return f"VECTOR({self.dimensions})"


class PortableVector(TypeDecorator):
    """JSON on SQLite tests; native VECTOR on Cockroach/PostgreSQL runtimes."""

    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int = 1024):
        self.dimensions = dimensions
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name in {"postgresql", "cockroachdb"}:
            return dialect.type_descriptor(_CockroachVector(self.dimensions))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return None
        values = [float(item) for item in value]
        if len(values) != self.dimensions:
            raise ValueError(f"Embedding must contain {self.dimensions} dimensions.")
        if dialect.name in {"postgresql", "cockroachdb"}:
            return json.dumps(values, separators=(",", ":"))
        return values

    def process_result_value(self, value: Any, _dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = json.loads(value)
        return [float(item) for item in value]
