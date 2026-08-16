import io
import json
import os
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.services.embeddings import TitanEmbeddingProvider, recursive_structure_chunks
from app.services.token_accounting import BedrockTokenAccounting, ModelCallObserver, canonical_tool_schemas, heuristic_input_tokens, token_error_statistics
from app.tools import CAREER_AGENT_TOOLS


class TokenAndChunkingTests(unittest.TestCase):
    def test_provider_aware_count_and_heuristic_fallback_include_tools(self):
        class Client:
            def count_tokens(self, **kwargs): self.kwargs=kwargs; return {"inputTokens":123}
        client=Client(); service=BedrockTokenAccounting(client)
        result=service.count_message_input("model",[HumanMessage(content="hello")],tools=list(CAREER_AGENT_TOOLS),exact_trigger=0)
        self.assertEqual(result.input_tokens,123); self.assertEqual(result.count_source,"bedrock_count_tokens")
        self.assertIn("toolConfig",client.kwargs["input"]["converse"])
        class Failing:
            def count_tokens(self, **_): raise RuntimeError
        fallback=BedrockTokenAccounting(Failing()).count_message_input("model",[HumanMessage(content="hello")],tools=list(CAREER_AGENT_TOOLS),exact_trigger=0)
        self.assertEqual(fallback.count_source,"heuristic_fallback")
        self.assertGreater(heuristic_input_tokens([HumanMessage(content="hello")],tools=list(CAREER_AGENT_TOOLS)),heuristic_input_tokens([HumanMessage(content="hello")],tools=[]))
        self.assertTrue(canonical_tool_schemas())

    def test_provider_aware_count_can_use_separate_supported_model(self):
        class Client:
            def count_tokens(self, **kwargs):
                self.kwargs = kwargs
                return {"inputTokens": 7}

        client = Client()
        with patch.dict(os.environ, {"BEDROCK_COUNT_TOKENS_MODEL": "direct-token-model"}):
            result = BedrockTokenAccounting(client).count_message_input(
                "generation-profile",
                [HumanMessage(content="hello")],
                tools=[],
                exact_trigger=0,
            )
        self.assertEqual(result.count_source, "bedrock_count_tokens")
        self.assertEqual(client.kwargs["modelId"], "direct-token-model")

    def test_final_preflight_counts_tool_messages(self):
        base=[HumanMessage(content="request")]
        with_tool=[*base,ToolMessage(content="x"*1000,tool_call_id="c")]
        self.assertGreater(heuristic_input_tokens(with_tool,tools=[]),heuristic_input_tokens(base,tools=[]))
        converse=BedrockTokenAccounting._converse_input([
            AIMessage(content="",tool_calls=[{"name":"search_jobs","args":{"request":{"target_roles":["ML"]}},"id":"call-1","type":"tool_call"}]),
            ToolMessage(content='{"ok":true}',tool_call_id="call-1"),
        ],[])['converse']['messages']
        self.assertEqual(converse[0]["content"][0]["toolUse"]["toolUseId"],"call-1")
        self.assertEqual(converse[1]["content"][0]["toolResult"]["toolUseId"],"call-1")

    def test_actual_usage_persisted_without_private_content(self):
        engine=create_database_engine("sqlite://"); init_db(engine); repo=ProfileRepository(create_session_factory(engine)); user=repo.get_or_create_user("Ada","metrics@example.com")
        class Response:
            usage_metadata={"input_tokens":10,"output_tokens":3,"total_tokens":13,"input_token_details":{"cache_read":2}}
            response_metadata={"stopReason":"end_turn"}
        class Runnable:
            def invoke(self,_): return Response()
        class Accounting:
            def count_message_input(self,*_,**__):
                from app.services.token_accounting import TokenCountResult
                return TokenCountResult(9,"bedrock_count_tokens")
        response=ModelCallObserver(repo,Accounting()).invoke(Runnable(),[HumanMessage(content="PRIVATE PROFILE TEXT")],user_id=user["user_id"],conversation_id=None,run_id=None,stage="agent_model",model_type="reasoning",model_id="model")
        metric=repo.list_model_call_metrics(user["user_id"])[0]
        self.assertEqual(metric["actual_input_tokens"],10); self.assertEqual(metric["cache_read_input_tokens"],2)
        self.assertFalse(any("PRIVATE" in str(value) for value in metric.values()))
        engine.dispose()

    def test_telemetry_failure_cannot_break_model_response_and_cache_absence_is_null(self):
        class Repo:
            def create_model_call_metric(self,*_,**__): raise RuntimeError("metrics offline")
        class Response:
            content="ok"; usage_metadata={"input_tokens":2,"output_tokens":1}; response_metadata={}
        class Runnable:
            def invoke(self,_): return Response()
        class Accounting:
            def count_message_input(self,*_,**__):
                from app.services.token_accounting import TokenCountResult
                return TokenCountResult(2,"heuristic_fallback")
        response=ModelCallObserver(Repo(),Accounting()).invoke(Runnable(),[HumanMessage(content="hi")],user_id="u",conversation_id=None,run_id=None,stage="agent_model",model_type="reasoning",model_id="m")
        self.assertEqual(response.content,"ok")

    def test_token_error_percentiles(self):
        stats=token_error_statistics([{"actual_input_tokens":actual,"preflight_input_tokens":10} for actual in (9,10,12,20)])
        self.assertEqual(stats["count"],4); self.assertEqual(stats["p50"],0); self.assertEqual(stats["p95"],10); self.assertEqual(stats["maximum_underestimation"],10)

    def test_titan_input_count_captured_not_fabricated(self):
        class Client:
            def invoke_model(self,**_): return {"body":io.BytesIO(json.dumps({"embedding":[0.0]*1024,"inputTextTokenCount":17}).encode())}
        provider=TitanEmbeddingProvider(Client()); provider.embed("hello")
        self.assertEqual(provider.last_input_tokens,17)
        class NoCount:
            def invoke_model(self,**_): return {"body":io.BytesIO(json.dumps({"embedding":[0.0]*1024}).encode())}
        provider=TitanEmbeddingProvider(NoCount()); provider.embed("hello")
        self.assertIsNone(provider.last_input_tokens)

    def test_recursive_structure_chunking_boundaries_overlap_and_metadata(self):
        text="Experience\n\nBuilt a small application.\n\nSkills\n\nPython and SQL."
        chunks=recursive_structure_chunks(text,target_tokens=20,max_tokens=30,overlap_tokens=4)
        self.assertTrue(any(chunk.section_title=="Experience" for chunk in chunks)); self.assertTrue(any(chunk.section_title=="Skills" for chunk in chunks))
        self.assertTrue(all(chunk.chunking_strategy=="recursive_structure_v1" and chunk.token_count<=30 for chunk in chunks))
        paragraph="A short natural paragraph that stays together."
        self.assertEqual(len(recursive_structure_chunks(paragraph,target_tokens=100,max_tokens=120)),1)
        forced=recursive_structure_chunks("x"*1000,target_tokens=20,max_tokens=20,overlap_tokens=5)
        self.assertGreater(len(forced),1); self.assertTrue(all(chunk.token_count<=20 for chunk in forced))
        self.assertTrue(forced[0].text[-10:] in forced[1].text)

    def test_multilingual_json_url_and_empty(self):
        text="教育\n\n这是一个机器学习项目。"*50+"\n\n{\"url\":\"https://example.com/a?x=1&y=2\",\"code\":\"print(1)\"}"
        chunks=recursive_structure_chunks(text,target_tokens=40,max_tokens=60,overlap_tokens=8)
        self.assertTrue(chunks); self.assertTrue(all(chunk.token_count<=60 for chunk in chunks)); self.assertEqual(recursive_structure_chunks(""),[])


if __name__ == "__main__": unittest.main()
