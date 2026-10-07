"""Official MCP stdio server for the Finnhub stock-research tools."""

from typing import Annotated, Any, Dict, NotRequired, TypedDict, cast

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from system_prompt import STOCK_RESEARCH_SYSTEM_PROMPT
from tool_handlers import (
    get_company_news as handle_company_news,
    get_company_profile as handle_company_profile,
    get_stock_quote as handle_stock_quote,
    search_symbol as handle_symbol_search,
)
from tools_definitions import TOOL_DEFINITIONS


class ToolResult(TypedDict):
    """Structured success or error envelope returned by every MCP tool."""

    ok: bool
    tool: str
    data: NotRequired[Any]
    error: NotRequired[Dict[str, Any]]


_TOOL_DESCRIPTIONS = {
    definition["name"]: definition["description"]
    for definition in TOOL_DEFINITIONS
}

TickerSymbol = Annotated[
    str,
    Field(
        min_length=1,
        max_length=15,
        pattern=r"^[A-Za-z][A-Za-z0-9.-]*$",
        description="Resolved US stock ticker, for example 'NVDA'.",
    ),
]

server = MCPServer(
    name="stocks-research",
    title="Finnhub US Stock Research",
    description="Research US stocks using current Finnhub market data.",
    instructions=STOCK_RESEARCH_SYSTEM_PROMPT,
    version="1.0.0",
)


@server.tool(
    name="search_symbol",
    description=_TOOL_DESCRIPTIONS["search_symbol"],
    structured_output=True,
)
def search_symbol(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Company name or search text, for example 'Nvidia'.",
        ),
    ],
) -> ToolResult:
    """Resolve a company query to one or more ticker candidates."""
    return cast(ToolResult, handle_symbol_search(query))


@server.tool(
    name="get_stock_quote",
    description=_TOOL_DESCRIPTIONS["get_stock_quote"],
    structured_output=True,
)
def get_stock_quote(symbol: TickerSymbol) -> ToolResult:
    """Return current quote data for a resolved ticker."""
    return cast(ToolResult, handle_stock_quote(symbol))


@server.tool(
    name="get_company_profile",
    description=_TOOL_DESCRIPTIONS["get_company_profile"],
    structured_output=True,
)
def get_company_profile(symbol: TickerSymbol) -> ToolResult:
    """Return company identity and fundamental profile data."""
    return cast(ToolResult, handle_company_profile(symbol))


@server.tool(
    name="get_company_news",
    description=_TOOL_DESCRIPTIONS["get_company_news"],
    structured_output=True,
)
def get_company_news(
    symbol: TickerSymbol,
    days_back: Annotated[
        int,
        Field(
            ge=1,
            le=365,
            description="Calendar-day lookback period ending today.",
        ),
    ] = 7,
) -> ToolResult:
    """Return recent company news for a resolved ticker."""
    return cast(ToolResult, handle_company_news(symbol, days_back))


def main() -> None:
    """Run the MCP server over standard input/output."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
