from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import boto3
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


@dataclass(frozen=True)
class TokenCountResult:
    input_tokens: int
    count_source: Literal["bedrock_count_tokens", "heuristic_fallback"]


def heuristic_text_tokens(text: str) -> int:
    """Conservative estimator used only when provider-aware counting is unavailable."""
    return max(1, (len(text.encode("utf-8")) + 2) // 3)


def canonical_tool_schemas(tools: list[Any] | None = None) -> list[dict[str, Any]]:
    if tools is None:
        from app.tools import CAREER_AGENT_TOOLS
        tools = list(CAREER_AGENT_TOOLS)
    schemas = []
    for tool in tools:
        schema = tool.tool_call_schema.model_json_schema()
        schemas.append({"name": tool.name, "description": tool.description or "", "input_schema": schema})
    return schemas


def heuristic_input_tokens(messages: list[BaseMessage], *, tools: list[Any] | None = None) -> int:
    message_payload = [{"role": message.type, "content": message.content} for message in messages]
    total = heuristic_text_tokens(json.dumps(message_payload, sort_keys=True, default=str, ensure_ascii=False))
    schemas = canonical_tool_schemas(tools)
    if schemas:
        total += heuristic_text_tokens(json.dumps(schemas, sort_keys=True, default=str, ensure_ascii=False))
    return total


class BedrockTokenAccounting:
    def __init__(self, client=None, *, region: str | None = None):
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.client = client

    def _client(self):
        if self.client is None:
            self.client = boto3.client("bedrock-runtime", region_name=self.region)
        return self.client

    @staticmethod
    def _converse_input(messages: list[BaseMessage], tools: list[Any] | None) -> dict[str, Any]:
        system: list[dict[str, str]] = []
        converse_messages: list[dict[str, Any]] = []
        for message in messages:
            text_content = str(message.content or "")
            if message.type == "system":
                if text_content:
                    system.append({"text": text_content})
            elif isinstance(message, ToolMessage):
                block = {"toolResult": {"toolUseId": message.tool_call_id, "content": [{"text": text_content}]}}
                if converse_messages and converse_messages[-1]["role"] == "user" and all("toolResult" in item for item in converse_messages[-1]["content"]):
                    converse_messages[-1]["content"].append(block)
                else:
                    converse_messages.append({"role": "user", "content": [block]})
            elif isinstance(message, AIMessage):
                content: list[dict[str, Any]] = []
                if text_content:
                    content.append({"text": text_content})
                for call in message.tool_calls or []:
                    content.append({"toolUse": {"toolUseId": str(call["id"]), "name": str(call["name"]), "input": dict(call.get("args") or {})}})
                if content:
                    converse_messages.append({"role": "assistant", "content": content})
            else:
                converse_messages.append({"role": "user", "content": [{"text": text_content}]})
        converse: dict[str, Any] = {"messages": converse_messages}
        if system:
            converse["system"] = system
        schemas = canonical_tool_schemas(tools)
        if schemas:
            converse["toolConfig"] = {"tools": [{"toolSpec": {"name": item["name"], "description": item["description"], "inputSchema": {"json": item["input_schema"]}}} for item in schemas]}
        return {"converse": converse}

    def count_message_input(self, model_id: str, messages: list[BaseMessage], *, tools: list[Any] | None = None, exact_trigger: int | None = None) -> TokenCountResult:
        rough = heuristic_input_tokens(messages, tools=tools)
        if exact_trigger is not None and rough < exact_trigger:
            return TokenCountResult(rough, "heuristic_fallback")
        try:
            response = self._client().count_tokens(modelId=model_id, input=self._converse_input(messages, tools))
            return TokenCountResult(int(response["inputTokens"]), "bedrock_count_tokens")
        except Exception:
            return TokenCountResult(rough, "heuristic_fallback")


def extract_usage(response: Any) -> dict[str, int | str | None]:
    metadata = getattr(response, "usage_metadata", None)
    response_metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    response_metadata = response_metadata if isinstance(response_metadata, Mapping) else {}
    usage = response_metadata.get("usage") or metadata
    usage = usage if isinstance(usage, Mapping) else {}
    input_tokens = usage.get("input_tokens", usage.get("inputTokens"))
    output_tokens = usage.get("output_tokens", usage.get("outputTokens"))
    total_tokens = usage.get("total_tokens", usage.get("totalTokens"))
    details = usage.get("input_token_details") or usage.get("inputTokenDetails") or {}
    details = details if isinstance(details, Mapping) else {}
    return {
        "actual_input_tokens": int(input_tokens) if input_tokens is not None else None,
        "actual_output_tokens": int(output_tokens) if output_tokens is not None else None,
        "actual_total_tokens": int(total_tokens) if total_tokens is not None else (int(input_tokens) + int(output_tokens) if input_tokens is not None and output_tokens is not None else None),
        "cache_read_input_tokens": details.get("cache_read", details.get("cacheReadInputTokens")),
        "cache_write_input_tokens": details.get("cache_creation", details.get("cacheWriteInputTokens")),
        "stop_reason": response_metadata.get("stopReason") or response_metadata.get("stop_reason"),
    }


def token_error_statistics(metrics: list[dict[str, Any]]) -> dict[str, float | int | None]:
    errors = sorted(
        int(item["actual_input_tokens"]) - int(item["preflight_input_tokens"])
        for item in metrics
        if item.get("actual_input_tokens") is not None and item.get("preflight_input_tokens") is not None
    )
    if not errors:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "maximum_underestimation": None}
    def percentile(value: float) -> int:
        return errors[min(len(errors) - 1, max(0, math.ceil(value * len(errors)) - 1))]
    return {
        "count": len(errors),
        "mean": sum(errors) / len(errors),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "maximum_underestimation": max(0, max(errors)),
    }


class ModelCallObserver:
    def __init__(self, repository: Any, accounting: BedrockTokenAccounting | None = None):
        self.repository = repository
        self.accounting = accounting or BedrockTokenAccounting()

    def invoke(self, runnable: Any, messages: list[BaseMessage], *, user_id: str, conversation_id: str | None, run_id: str | None, stage: str, model_type: str, model_id: str, tools: list[Any] | None = None, compression_triggered: bool = False) -> Any:
        rough = heuristic_input_tokens(messages, tools=tools)
        context_limit = int(os.getenv("CONTEXT_MODEL_LIMIT_TOKENS") or 32000)
        reserved = int(os.getenv("CONTEXT_RESERVED_OUTPUT_TOKENS", "4096"))
        margin = int(os.getenv("CONTEXT_SAFETY_MARGIN_TOKENS", "8192"))
        threshold = max(1000, int((context_limit - reserved - margin) * float(os.getenv("CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.75"))))
        preflight = self.accounting.count_message_input(model_id, messages, tools=tools, exact_trigger=max(0, threshold - margin))
        started = time.monotonic()
        response = None
        error_type = None
        try:
            response = runnable.invoke(messages)
            return response
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            usage = extract_usage(response) if response is not None else {}
            try:
                self.repository.create_model_call_metric(
                    user_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    stage=stage,
                    model_type=model_type,
                    model_id=model_id,
                    region=os.getenv("AWS_REGION"),
                    rough_estimated_input_tokens=rough,
                    preflight_input_tokens=preflight.input_tokens,
                    preflight_count_source=preflight.count_source,
                    model_context_limit=context_limit,
                    reserved_output_tokens=reserved,
                    safety_margin_tokens=margin,
                    compression_threshold=threshold,
                    compression_triggered=compression_triggered,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_type=error_type,
                    **usage,
                )
            except Exception:
                pass
