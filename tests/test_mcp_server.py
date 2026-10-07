"""Protocol-level tests for the stock-research MCP server."""

import os
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_ROOT / "mcp_server.py"
EXPECTED_TOOLS = {
    "search_symbol",
    "get_stock_quote",
    "get_company_profile",
    "get_company_news",
}


class MCPServerProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery_and_structured_error(self) -> None:
        environment = os.environ.copy()
        environment.pop("FINNHUB_API_KEY", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PATH)],
            cwd=PROJECT_ROOT,
            env=environment,
        )

        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                listed_tools = await session.list_tools()
                tools_by_name = {tool.name: tool for tool in listed_tools.tools}
                self.assertEqual(set(tools_by_name), EXPECTED_TOOLS)
                self.assertEqual(
                    tools_by_name["search_symbol"].input_schema["properties"][
                        "query"
                    ]["maxLength"],
                    100,
                )
                self.assertEqual(
                    tools_by_name["get_company_news"].input_schema["properties"][
                        "days_back"
                    ]["default"],
                    7,
                )

                result = await session.call_tool(
                    "get_stock_quote",
                    {"symbol": "NVDA"},
                )
                self.assertFalse(result.is_error)
                self.assertIsNotNone(result.structured_content)
                assert result.structured_content is not None
                self.assertFalse(result.structured_content["ok"])
                self.assertEqual(
                    result.structured_content["error"]["code"],
                    "CONFIGURATION_ERROR",
                )


if __name__ == "__main__":
    unittest.main()
