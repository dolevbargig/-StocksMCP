"""Framework-neutral MCP tool definitions for US stock research."""

from typing import Any, Dict, List


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "search_symbol",
        "description": (
            "Search Finnhub for ticker symbols matching a company name or other "
            "natural-language query. Use this before company-data tools when the "
            "user provides a company name instead of an unambiguous ticker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "description": (
                        "Company name or search text, for example 'Nvidia' or "
                        "'Tesla'."
                    ),
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stock_quote",
        "description": (
            "Get the latest Finnhub quote for a US stock ticker, including the "
            "current price, absolute and percentage change, intraday high and "
            "low, open, and previous close."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 15,
                    "pattern": "^[A-Za-z][A-Za-z0-9.-]*$",
                    "description": "Resolved US stock ticker, for example 'NVDA'.",
                }
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_company_profile",
        "description": (
            "Get a Finnhub company profile for a US stock ticker, including the "
            "company name, exchange, industry, country, currency, and market "
            "capitalization."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 15,
                    "pattern": "^[A-Za-z][A-Za-z0-9.-]*$",
                    "description": "Resolved US stock ticker, for example 'TSLA'.",
                }
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_company_news",
        "description": (
            "Get recent Finnhub news headlines and summaries for a US stock "
            "ticker over a configurable lookback period."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 15,
                    "pattern": "^[A-Za-z][A-Za-z0-9.-]*$",
                    "description": "Resolved US stock ticker, for example 'AAPL'.",
                },
                "days_back": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "default": 7,
                    "description": (
                        "Number of calendar days to include, ending today. "
                        "Defaults to 7."
                    ),
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
]
