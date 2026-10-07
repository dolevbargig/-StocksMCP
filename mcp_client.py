"""Request-scoped MCP stdio client used by the local chat backend."""

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Dict, List, Optional, Type

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_PATH = PROJECT_ROOT / "mcp_server.py"
MAX_TEXT_FALLBACK_LENGTH = 4_000
MCP_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
        "VIRTUAL_ENV",
    }
)


def _build_mcp_environment(
    finnhub_api_key: str,
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    source = os.environ if environ is None else environ
    environment = {
        key: value
        for key, value in source.items()
        if key in MCP_ENV_ALLOWLIST
    }
    environment["FINNHUB_API_KEY"] = finnhub_api_key
    return environment


class MCPClientError(RuntimeError):
    """Raised when the chat backend cannot use the MCP server safely."""


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any]


class StockMCPClient:
    """Own one MCP server subprocess for the duration of a chat request."""

    def __init__(self, *, finnhub_api_key: str) -> None:
        self._finnhub_api_key = finnhub_api_key
        self._exit_stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self.instructions = ""
        self.tools: List[MCPTool] = []

    async def __aenter__(self) -> "StockMCPClient":
        environment = _build_mcp_environment(self._finnhub_api_key)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PATH)],
            cwd=PROJECT_ROOT,
            env=environment,
        )

        self._exit_stack = AsyncExitStack()
        try:
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(parameters)
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            initialization = await self._session.initialize()
            self.instructions = initialization.instructions or ""
            listed = await self._session.list_tools()
            self.tools = [
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                )
                for tool in listed.tools
            ]
        except Exception as error:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            raise MCPClientError("The stock tools could not be started.") from error

        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if self._session is None:
            raise MCPClientError("The stock tools are not connected.")

        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as error:
            raise MCPClientError(f"The '{name}' tool could not be executed.") from error

        if result.structured_content is not None:
            return dict(result.structured_content)

        text_parts = [
            getattr(item, "text", "")
            for item in result.content
            if getattr(item, "type", None) == "text"
        ]
        text = "\n".join(part for part in text_parts if part)[
            :MAX_TEXT_FALLBACK_LENGTH
        ]
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return {
            "ok": False,
            "tool": name,
            "error": {
                "code": "MCP_TOOL_ERROR" if result.is_error else "MCP_INVALID_RESULT",
                "message": text or "The MCP tool returned no structured result.",
                "retryable": False,
            },
        }
