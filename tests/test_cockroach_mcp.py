import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from app.integrations.cockroach_cloud_mcp import CockroachCloudMCPDiagnostics
from app.tools import CAREER_AGENT_TOOLS


class Tool:
    def __init__(self,name): self.name=name
class Tools:
    tools=[Tool("list_tables"),Tool("select_query"),Tool("insert_rows")]
class Session:
    def __init__(self): self.calls=[]
    async def list_tools(self): return Tools()
    async def call_tool(self,name,args): self.calls.append((name,args)); return {"ok":True}


class CockroachMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.session=Session()
    def factory(self, captured):
        @asynccontextmanager
        async def create(url,headers):
            captured.update(url=url,headers=headers); yield self.session
        return create

    async def test_managed_mcp_config_discovery_and_read_policy(self):
        captured={}
        with patch.dict(os.environ,{"COCKROACH_CLOUD_MCP_ENABLED":"true","COCKROACH_CLOUD_CLUSTER_ID":"cluster-1","COCKROACH_CLOUD_MCP_API_KEY":"top-secret"}):
            client=CockroachCloudMCPDiagnostics(session_factory=self.factory(captured))
            self.assertEqual(await client.list_capabilities(),["insert_rows","list_tables","select_query"])
            await client.invoke_read("select_query",{"query":"SELECT 1","cluster_id":"cluster-1"})
            with self.assertRaises(ValueError): await client.invoke_read("insert_rows",{})
            with self.assertRaises(ValueError): await client.invoke_read("select_query",{"query":"DELETE FROM users"})
            with self.assertRaises(ValueError): await client.invoke_read("select_query",{"query":"SELECT email FROM users LIMIT 10"})
            with self.assertRaises(ValueError): await client.invoke_read("select_query",{"query":"SELECT * FROM crdb_internal.cluster_transactions LIMIT 101"})
            with self.assertRaises(ValueError): await client.invoke_read("show_running_queries",{})
        self.assertEqual(captured["url"],"https://cockroachlabs.cloud/mcp"); self.assertEqual(captured["headers"]["mcp-cluster-id"],"cluster-1")
        self.assertEqual(self.session.calls[0][1]["query"], "SELECT 1 LIMIT 100")
        self.assertNotIn("top-secret",repr(self.session.calls))
        self.assertNotIn("Cockroach", " ".join(tool.name for tool in CAREER_AGENT_TOOLS))


if __name__ == "__main__": unittest.main()
