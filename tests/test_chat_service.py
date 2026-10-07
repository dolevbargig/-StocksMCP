"""Tests for Azure Responses and MCP orchestration without provider calls."""

import json
import unittest
from typing import Any, Dict, List
from unittest.mock import Mock

from azure_responses import AzureResponsesClient, AzureResponsesError
from chat_service import ChatService, ChatServiceError, MAX_TOTAL_TOOL_CALLS
from config import Settings
from mcp_client import MCPTool


SETTINGS = Settings(
    finnhub_api_key="finnhub-test",
    azure_openai_api_key="azure-test",
    azure_openai_endpoint="https://example.invalid/responses?api-version=preview",
    azure_openai_model="gpt-4.1",
)

SYMBOL_TOOL = MCPTool(
    name="search_symbol",
    description="Resolve a company name.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
        "additionalProperties": False,
    },
)
QUOTE_TOOL = MCPTool(
    name="get_stock_quote",
    description="Get a quote.",
    input_schema={
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
        "additionalProperties": False,
    },
)
PROFILE_TOOL = MCPTool(
    name="get_company_profile",
    description="Get a profile.",
    input_schema=QUOTE_TOOL.input_schema,
)
NEWS_TOOL = MCPTool(
    name="get_company_news",
    description="Get news.",
    input_schema={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "days_back": {"type": "integer", "default": 7},
        },
        "required": ["symbol"],
        "additionalProperties": False,
    },
)


class FakeAzure:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.payloads: List[Dict[str, Any]] = []

    async def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.payloads.append(payload)
        return self.responses.pop(0)


class FakeMCP:
    def __init__(self) -> None:
        self.instructions = "Use tools and never invent market data."
        self.tools = [SYMBOL_TOOL, QUOTE_TOOL, PROFILE_TOOL, NEWS_TOOL]
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    async def __aenter__(self) -> "FakeMCP":
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        return None

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "search_symbol":
            data = {
                "count": 1,
                "candidates": [{"symbol": "NVDA", "description": "NVIDIA"}],
            }
        elif name == "get_stock_quote":
            data = {"symbol": "NVDA", "current_price": 123.45}
        elif name == "get_company_profile":
            data = {"symbol": "NVDA", "company_name": "NVIDIA Corp"}
        else:
            data = {"symbol": "NVDA", "count": 2, "articles": []}
        return {"ok": True, "tool": name, "data": data}


def final_response(text: str) -> Dict[str, Any]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


def function_call(name: str, call_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "arguments": json.dumps(arguments),
    }


class ChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_answer_uses_discovered_tools_and_instructions(self) -> None:
        azure = FakeAzure([final_response("Hello")])
        mcp = FakeMCP()
        service = ChatService(
            SETTINGS,
            azure_client=azure,
            mcp_client_factory=lambda: mcp,
        )

        result = await service.chat([{"role": "user", "content": "Hello"}])

        self.assertEqual(result.answer, "Hello")
        self.assertEqual(result.trace, [])
        payload = azure.payloads[0]
        self.assertEqual(payload["instructions"], mcp.instructions)
        self.assertEqual(payload["model"], "gpt-4.1")
        self.assertFalse(payload["store"])
        self.assertEqual(
            {tool["name"] for tool in payload["tools"]},
            {tool.name for tool in mcp.tools},
        )
        self.assertEqual(
            payload["tools"][0]["parameters"],
            SYMBOL_TOOL.input_schema,
        )

    async def test_symbol_then_parallel_research_calls(self) -> None:
        azure = FakeAzure(
            [
                {"output": [function_call("search_symbol", "call-1", {"query": "Nvidia"})]},
                {
                    "output": [
                        function_call("get_stock_quote", "call-2", {"symbol": "NVDA"}),
                        function_call("get_company_profile", "call-3", {"symbol": "NVDA"}),
                        function_call(
                            "get_company_news",
                            "call-4",
                            {"symbol": "NVDA", "days_back": 7},
                        ),
                    ]
                },
                final_response("NVIDIA research summary"),
            ]
        )
        mcp = FakeMCP()
        service = ChatService(
            SETTINGS,
            azure_client=azure,
            mcp_client_factory=lambda: mcp,
        )

        result = await service.chat(
            [{"role": "user", "content": "Research Nvidia"}]
        )

        self.assertEqual(result.answer, "NVIDIA research summary")
        self.assertEqual(
            [name for name, _arguments in mcp.calls],
            [
                "search_symbol",
                "get_stock_quote",
                "get_company_profile",
                "get_company_news",
            ],
        )
        self.assertEqual(len(result.trace), 4)
        second_turn_outputs = [
            item
            for item in azure.payloads[1]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(second_turn_outputs[0]["call_id"], "call-1")
        third_turn_outputs = [
            item
            for item in azure.payloads[2]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(
            {item["call_id"] for item in third_turn_outputs},
            {"call-1", "call-2", "call-3", "call-4"},
        )

    async def test_invalid_arguments_are_returned_to_model_without_mcp_call(self) -> None:
        azure = FakeAzure(
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "name": "get_stock_quote",
                            "call_id": "bad-call",
                            "arguments": "not json",
                        }
                    ]
                },
                final_response("I could not run the quote tool."),
            ]
        )
        mcp = FakeMCP()
        service = ChatService(
            SETTINGS,
            azure_client=azure,
            mcp_client_factory=lambda: mcp,
        )

        result = await service.chat([{"role": "user", "content": "Quote"}])

        self.assertEqual(mcp.calls, [])
        self.assertEqual(result.trace[0].status, "error")
        output_item = azure.payloads[1]["input"][-1]
        decoded = json.loads(output_item["output"])
        self.assertEqual(decoded["error"]["code"], "INVALID_TOOL_ARGUMENTS")

    async def test_unknown_tool_is_not_invoked(self) -> None:
        azure = FakeAzure(
            [
                {"output": [function_call("delete_portfolio", "call-1", {})]},
                final_response("That tool is unavailable."),
            ]
        )
        mcp = FakeMCP()
        service = ChatService(
            SETTINGS,
            azure_client=azure,
            mcp_client_factory=lambda: mcp,
        )

        result = await service.chat([{"role": "user", "content": "Delete it"}])

        self.assertEqual(mcp.calls, [])
        self.assertEqual(result.trace[0].summary, "Tool returned UNKNOWN_TOOL.")

    async def test_total_call_limit_is_enforced(self) -> None:
        calls = [
            function_call("get_stock_quote", f"call-{index}", {"symbol": "NVDA"})
            for index in range(MAX_TOTAL_TOOL_CALLS + 1)
        ]
        azure = FakeAzure([{"output": calls}])
        service = ChatService(
            SETTINGS,
            azure_client=azure,
            mcp_client_factory=FakeMCP,
        )

        with self.assertRaises(ChatServiceError) as context:
            await service.chat([{"role": "user", "content": "Research"}])
        self.assertEqual(context.exception.code, "TOOL_CALL_LIMIT")

    async def test_empty_model_response_is_sanitized(self) -> None:
        service = ChatService(
            SETTINGS,
            azure_client=FakeAzure([{"output": []}]),
            mcp_client_factory=FakeMCP,
        )
        with self.assertRaises(ChatServiceError) as context:
            await service.chat([{"role": "user", "content": "Hello"}])
        self.assertEqual(context.exception.code, "MODEL_EMPTY_RESPONSE")


class AzureResponsesClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_uses_exact_endpoint_and_api_key_header(self) -> None:
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {"output": []}
        request = Mock(return_value=response)
        client = AzureResponsesClient(
            endpoint=SETTINGS.azure_openai_endpoint,
            api_key=SETTINGS.azure_openai_api_key,
            request=request,
        )

        result = await client.create({"model": "gpt-4.1", "input": []})

        self.assertEqual(result, {"output": []})
        self.assertEqual(request.call_args.args[0], SETTINGS.azure_openai_endpoint)
        self.assertEqual(
            request.call_args.kwargs["headers"]["api-key"],
            SETTINGS.azure_openai_api_key,
        )

    async def test_authentication_failure_does_not_expose_response_body(self) -> None:
        response = Mock(status_code=401, ok=False, text="secret provider body")
        client = AzureResponsesClient(
            endpoint=SETTINGS.azure_openai_endpoint,
            api_key=SETTINGS.azure_openai_api_key,
            request=Mock(return_value=response),
        )

        with self.assertRaises(AzureResponsesError) as context:
            await client.create({"model": "gpt-4.1", "input": []})

        self.assertEqual(context.exception.code, "AZURE_AUTHENTICATION_ERROR")
        self.assertNotIn("secret provider body", context.exception.public_message)
        self.assertNotIn(SETTINGS.azure_openai_api_key, context.exception.public_message)


if __name__ == "__main__":
    unittest.main()
