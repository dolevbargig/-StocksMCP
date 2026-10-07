"""Stateless Azure Responses and MCP tool-call orchestration."""

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from jsonschema import SchemaError, ValidationError, validate

from azure_responses import AzureResponsesClient
from config import Settings
from mcp_client import MCPClientError, MCPTool, StockMCPClient


MAX_TOOL_ROUNDS = 5
MAX_TOTAL_TOOL_CALLS = 12
MAX_TRACE_VALUE_LENGTH = 160


class ChatServiceError(RuntimeError):
    """A safe, user-facing orchestration failure."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class AzureClient(Protocol):
    async def create(self, payload: Dict[str, Any]) -> Dict[str, Any]: ...


class MCPClient(Protocol):
    instructions: str
    tools: List[MCPTool]

    async def __aenter__(self) -> "MCPClient": ...

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None: ...

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class ToolTrace:
    tool: str
    arguments: Dict[str, Any]
    status: str
    summary: str


@dataclass(frozen=True)
class ChatResult:
    answer: str
    trace: List[ToolTrace]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "trace": [asdict(item) for item in self.trace],
        }


class ChatService:
    """Run one complete Azure/MCP exchange for a stateless chat request."""

    def __init__(
        self,
        settings: Settings,
        *,
        azure_client: Optional[AzureClient] = None,
        mcp_client_factory: Optional[Callable[[], MCPClient]] = None,
    ) -> None:
        self._settings = settings
        self._azure = azure_client or AzureResponsesClient(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )
        self._mcp_client_factory = mcp_client_factory or (
            lambda: StockMCPClient(finnhub_api_key=settings.finnhub_api_key)
        )

    async def chat(self, messages: Sequence[Mapping[str, str]]) -> ChatResult:
        self._settings.require_chat_configuration()

        try:
            async with self._mcp_client_factory() as mcp_client:
                return await self._run_tool_loop(messages, mcp_client)
        except ChatServiceError:
            raise
        except MCPClientError as error:
            raise ChatServiceError("MCP_UNAVAILABLE", str(error)) from error

    async def _run_tool_loop(
        self,
        messages: Sequence[Mapping[str, str]],
        mcp_client: MCPClient,
    ) -> ChatResult:
        tools_by_name = {tool.name: tool for tool in mcp_client.tools}
        azure_tools = [self._to_azure_tool(tool) for tool in mcp_client.tools]
        conversation: List[Dict[str, Any]] = [
            {"role": message["role"], "content": message["content"]}
            for message in messages
        ]
        trace: List[ToolTrace] = []
        total_calls = 0

        for _round in range(MAX_TOOL_ROUNDS):
            response = await self._azure.create(
                {
                    "model": self._settings.azure_openai_model,
                    "instructions": mcp_client.instructions,
                    "input": conversation,
                    "tools": azure_tools,
                    "tool_choice": "auto",
                    "parallel_tool_calls": True,
                    "store": False,
                }
            )
            output = response.get("output", [])
            function_calls = [
                item
                for item in output
                if isinstance(item, dict) and item.get("type") == "function_call"
            ]

            if not function_calls:
                answer = self._extract_final_text(response)
                if not answer:
                    raise ChatServiceError(
                        "MODEL_EMPTY_RESPONSE",
                        "The language model returned no final answer.",
                    )
                return ChatResult(answer=answer, trace=trace)

            total_calls += len(function_calls)
            if total_calls > MAX_TOTAL_TOOL_CALLS:
                raise ChatServiceError(
                    "TOOL_CALL_LIMIT",
                    "The model requested too many tool calls for one message.",
                )

            conversation.extend(item for item in output if isinstance(item, dict))
            tool_outputs: List[Dict[str, Any]] = []
            for function_call in function_calls:
                call_id = function_call.get("call_id")
                name = function_call.get("name")
                if not isinstance(call_id, str) or not call_id:
                    raise ChatServiceError(
                        "MODEL_INVALID_TOOL_CALL",
                        "The language model returned an invalid tool call.",
                    )

                tool_result, tool_trace = await self._execute_function_call(
                    mcp_client,
                    tools_by_name,
                    name,
                    function_call.get("arguments"),
                )
                trace.append(tool_trace)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(tool_result, ensure_ascii=True),
                    }
                )
            conversation.extend(tool_outputs)

        raise ChatServiceError(
            "TOOL_ROUND_LIMIT",
            "The model did not finish after the maximum number of tool rounds.",
        )

    async def _execute_function_call(
        self,
        mcp_client: MCPClient,
        tools_by_name: Mapping[str, MCPTool],
        name: Any,
        raw_arguments: Any,
    ) -> tuple[Dict[str, Any], ToolTrace]:
        safe_name = name if isinstance(name, str) else "unknown"
        tool = tools_by_name.get(safe_name)
        if tool is None:
            result = self._tool_call_error(
                safe_name,
                "UNKNOWN_TOOL",
                "The requested tool is not available.",
            )
            return result, self._trace_for_result(safe_name, {}, result)

        arguments, argument_error = self._parse_arguments(raw_arguments)
        if argument_error is not None:
            result = self._tool_call_error(
                safe_name,
                "INVALID_TOOL_ARGUMENTS",
                argument_error,
            )
            return result, self._trace_for_result(safe_name, {}, result)

        try:
            validate(instance=arguments, schema=tool.input_schema)
        except (ValidationError, SchemaError):
            result = self._tool_call_error(
                safe_name,
                "INVALID_TOOL_ARGUMENTS",
                "The tool arguments did not match the published schema.",
            )
            return result, self._trace_for_result(safe_name, arguments, result)

        try:
            result = await mcp_client.call_tool(safe_name, arguments)
        except MCPClientError:
            result = self._tool_call_error(
                safe_name,
                "MCP_TOOL_ERROR",
                "The tool could not be executed.",
                retryable=True,
            )
        return result, self._trace_for_result(safe_name, arguments, result)

    @staticmethod
    def _to_azure_tool(tool: MCPTool) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            "strict": False,
        }

    @staticmethod
    def _parse_arguments(raw_arguments: Any) -> tuple[Dict[str, Any], Optional[str]]:
        if not isinstance(raw_arguments, str):
            return {}, "The model did not provide JSON tool arguments."
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}, "The model provided malformed JSON tool arguments."
        if not isinstance(arguments, dict):
            return {}, "Tool arguments must be a JSON object."
        return arguments, None

    @staticmethod
    def _tool_call_error(
        tool: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "tool": tool,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        }

    @classmethod
    def _trace_for_result(
        cls,
        tool: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> ToolTrace:
        safe_arguments = {
            str(key): cls._safe_trace_value(value)
            for key, value in arguments.items()
        }
        if not result.get("ok"):
            error = result.get("error")
            code = error.get("code") if isinstance(error, Mapping) else "TOOL_ERROR"
            return ToolTrace(
                tool=tool,
                arguments=safe_arguments,
                status="error",
                summary=f"Tool returned {code}.",
            )

        data = result.get("data")
        summary = "Tool returned data successfully."
        if isinstance(data, Mapping):
            if data.get("company_name"):
                summary = f"Returned profile for {data['company_name']}."
            elif data.get("current_price") is not None:
                summary = f"Returned quote for {data.get('symbol', 'the symbol')}."
            elif data.get("count") is not None:
                summary = f"Returned {data['count']} result(s)."
        return ToolTrace(
            tool=tool,
            arguments=safe_arguments,
            status="success",
            summary=summary,
        )

    @staticmethod
    def _safe_trace_value(value: Any) -> Any:
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value[:MAX_TRACE_VALUE_LENGTH]
        return str(value)[:MAX_TRACE_VALUE_LENGTH]

    @staticmethod
    def _extract_final_text(response: Mapping[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        text_parts: List[str] = []
        output = response.get("output")
        if not isinstance(output, list):
            return ""
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, Mapping):
                    continue
                if content_item.get("type") == "output_text":
                    text = content_item.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
        return "\n".join(text_parts)
