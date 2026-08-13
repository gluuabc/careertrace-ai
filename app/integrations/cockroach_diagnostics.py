"""Compatibility import; diagnostics now use the real managed MCP boundary."""

from app.integrations.cockroach_cloud_mcp import CockroachCloudMCPDiagnostics

CockroachDiagnostics = CockroachCloudMCPDiagnostics

__all__ = ["CockroachCloudMCPDiagnostics", "CockroachDiagnostics"]
