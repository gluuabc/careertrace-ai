from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any


READ_TOOLS = {
    "list_tools", "list_clusters", "get_cluster", "list_databases", "list_tables",
    "get_table_schema", "select_query", "explain_query",
}
WRITE_SQL = re.compile(r"\b(?:insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|upsert)\b", re.I)
PRIVATE_RELATION = re.compile(
    r"\b(?:from|join)\s+(?!crdb_internal\.|pg_catalog\.|information_schema\.)[a-z_][\w.]*",
    re.I,
)
LIMIT = re.compile(r"\blimit\s+(\d+)\b", re.I)
MAX_DIAGNOSTIC_ROWS = 100
MAX_DIAGNOSTIC_SQL_CHARS = 10_000


class CockroachCloudMCPDiagnostics:
    """Developer-only read diagnostics over the official Streamable HTTP MCP client."""

    def __init__(self, *, session_factory: Callable[..., Any] | None = None):
        self.enabled = os.getenv("COCKROACH_CLOUD_MCP_ENABLED", "false").casefold() == "true"
        self.url = os.getenv("COCKROACH_CLOUD_MCP_URL", "https://cockroachlabs.cloud/mcp")
        self.cluster_id = os.getenv("COCKROACH_CLOUD_CLUSTER_ID", "").strip()
        self.api_key = os.getenv("COCKROACH_CLOUD_MCP_API_KEY", "").strip()
        self.session_factory = session_factory

    def headers(self) -> dict[str, str]:
        headers = {"mcp-cluster-id": self.cluster_id} if self.cluster_id else {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        if not self.enabled:
            raise RuntimeError("CockroachDB Cloud MCP diagnostics are disabled.")
        if self.session_factory is not None:
            async with self.session_factory(self.url, self.headers()) as session:
                yield session
            return
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx

        async with httpx.AsyncClient(
            headers=self.headers(),
            timeout=httpx.Timeout(20.0, connect=10.0),
        ) as http_client:
            async with streamable_http_client(
                self.url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session

    async def list_capabilities(self) -> list[str]:
        async with self._session() as session:
            response = await session.list_tools()
            return sorted(tool.name for tool in response.tools)

    async def invoke_read(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        if tool_name not in READ_TOOLS or tool_name == "list_tools":
            raise ValueError("Only approved read diagnostic tools may be invoked.")
        payload = dict(arguments or {})
        if self.cluster_id and payload.get("cluster_id") not in {None, self.cluster_id}:
            raise ValueError("Diagnostic cluster scope cannot be changed.")
        if tool_name in {"select_query", "explain_query"}:
            query = str(payload.get("query") or payload.get("sql") or "")
            if len(query) > MAX_DIAGNOSTIC_SQL_CHARS:
                raise ValueError("Diagnostic SQL exceeds the bounded input limit.")
            if WRITE_SQL.search(query) or not re.match(r"^\s*(?:select|explain)\b", query, re.I):
                raise ValueError("Only bounded SELECT or EXPLAIN diagnostics are allowed.")
            if ";" in query.rstrip(";"):
                raise ValueError("Multiple diagnostic statements are not allowed.")
            if PRIVATE_RELATION.search(query):
                raise ValueError("Diagnostics may query only approved system metadata sources.")
            if tool_name == "select_query":
                match = LIMIT.search(query)
                if match and int(match.group(1)) > MAX_DIAGNOSTIC_ROWS:
                    raise ValueError("Diagnostic row limit exceeds the approved maximum.")
                if not match:
                    query = query.rstrip().rstrip(";") + f" LIMIT {MAX_DIAGNOSTIC_ROWS}"
                if "query" in payload:
                    payload["query"] = query
                else:
                    payload["sql"] = query
        async with self._session() as session:
            return await session.call_tool(tool_name, payload)

    def run_read(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        return asyncio.run(self.invoke_read(tool_name, arguments))
