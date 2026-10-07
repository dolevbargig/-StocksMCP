"""Synchronous Finnhub handlers for the stock-research MCP tools."""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Mapping, Optional

import requests


FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
REQUEST_TIMEOUT_SECONDS = 10
MAX_QUERY_LENGTH = 100
MAX_SEARCH_RESULTS = 8
MAX_NEWS_ARTICLES = 10
MAX_NEWS_LOOKBACK_DAYS = 365
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")

ToolResult = Dict[str, Any]


def _error(
    tool: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    status_code: Optional[int] = None,
) -> ToolResult:
    error: Dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if status_code is not None:
        error["status_code"] = status_code
    return {"ok": False, "tool": tool, "error": error}


def _success(tool: str, data: Any) -> ToolResult:
    return {"ok": True, "tool": tool, "data": data}


def _validate_text(value: Any, field: str, tool: str) -> Optional[ToolResult]:
    if not isinstance(value, str) or not value.strip():
        return _error(
            tool,
            "INVALID_INPUT",
            f"'{field}' must be a non-empty string.",
        )
    return None


def _normalize_symbol(symbol: Any, tool: str) -> tuple[Optional[str], Optional[ToolResult]]:
    validation_error = _validate_text(symbol, "symbol", tool)
    if validation_error:
        return None, validation_error

    normalized = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(normalized):
        return None, _error(
            tool,
            "INVALID_SYMBOL",
            (
                "'symbol' must start with a letter and contain only letters, "
                "numbers, periods, or hyphens (maximum 15 characters)."
            ),
        )
    return normalized, None


def _request_finnhub(tool: str, path: str, params: Mapping[str, Any]) -> ToolResult:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return _error(
            tool,
            "CONFIGURATION_ERROR",
            "FINNHUB_API_KEY is not configured in the server environment.",
        )

    request_params = dict(params)
    request_params["token"] = api_key

    try:
        response = requests.get(
            f"{FINNHUB_BASE_URL}{path}",
            params=request_params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        return _error(
            tool,
            "UPSTREAM_TIMEOUT",
            "Finnhub did not respond before the request timed out.",
            retryable=True,
        )
    except requests.ConnectionError:
        return _error(
            tool,
            "UPSTREAM_CONNECTION_ERROR",
            "The server could not connect to Finnhub.",
            retryable=True,
        )
    except requests.RequestException:
        return _error(
            tool,
            "UPSTREAM_REQUEST_ERROR",
            "The Finnhub request failed before a valid response was received.",
            retryable=True,
        )

    if response.status_code == 429:
        return _error(
            tool,
            "RATE_LIMITED",
            "Finnhub rate limit exceeded. Try again later.",
            retryable=True,
            status_code=response.status_code,
        )
    if response.status_code in (401, 403):
        return _error(
            tool,
            "AUTHENTICATION_ERROR",
            "Finnhub rejected the configured API key.",
            status_code=response.status_code,
        )
    if not response.ok:
        return _error(
            tool,
            "UPSTREAM_HTTP_ERROR",
            f"Finnhub returned HTTP {response.status_code}.",
            retryable=response.status_code >= 500,
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError:
        return _error(
            tool,
            "INVALID_UPSTREAM_RESPONSE",
            "Finnhub returned a response that was not valid JSON.",
            retryable=True,
        )

    if isinstance(payload, dict) and payload.get("error"):
        provider_message = str(payload["error"])
        is_rate_limit = "limit" in provider_message.lower()
        return _error(
            tool,
            "RATE_LIMITED" if is_rate_limit else "UPSTREAM_API_ERROR",
            (
                "Finnhub rate limit exceeded. Try again later."
                if is_rate_limit
                else f"Finnhub rejected the request: {provider_message}"
            ),
            retryable=is_rate_limit,
        )

    return _success(tool, payload)


def search_symbol(query: str) -> ToolResult:
    """Resolve a natural-language company query to Finnhub symbol candidates."""
    tool = "search_symbol"
    validation_error = _validate_text(query, "query", tool)
    if validation_error:
        return validation_error

    normalized_query = query.strip()
    if len(normalized_query) > MAX_QUERY_LENGTH:
        return _error(
            tool,
            "INVALID_INPUT",
            f"'query' must be at most {MAX_QUERY_LENGTH} characters.",
        )

    result = _request_finnhub(tool, "/search", {"q": normalized_query})
    if not result["ok"]:
        return result

    payload = result["data"]
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        return _error(
            tool,
            "INVALID_UPSTREAM_RESPONSE",
            "Finnhub returned an unexpected symbol-search response.",
        )

    all_candidates = [
        {
            "symbol": item.get("symbol"),
            "display_symbol": item.get("displaySymbol"),
            "description": item.get("description"),
            "type": item.get("type"),
        }
        for item in payload["result"]
        if isinstance(item, dict) and item.get("symbol")
    ]
    if not all_candidates:
        return _error(
            tool,
            "SYMBOL_NOT_FOUND",
            f"No Finnhub symbols matched '{normalized_query}'.",
        )

    candidates = all_candidates[:MAX_SEARCH_RESULTS]
    return _success(
        tool,
        {
            "query": normalized_query,
            "count": len(candidates),
            "available_count": len(all_candidates),
            "truncated": len(all_candidates) > len(candidates),
            "candidates": candidates,
        },
    )


def get_stock_quote(symbol: str) -> ToolResult:
    """Get the latest available quote for a normalized ticker symbol."""
    tool = "get_stock_quote"
    normalized_symbol, validation_error = _normalize_symbol(symbol, tool)
    if validation_error:
        return validation_error

    result = _request_finnhub(tool, "/quote", {"symbol": normalized_symbol})
    if not result["ok"]:
        return result

    payload = result["data"]
    if not isinstance(payload, dict) or payload.get("c") is None:
        return _error(
            tool,
            "QUOTE_NOT_FOUND",
            f"No quote data was found for symbol '{normalized_symbol}'.",
        )

    return _success(
        tool,
        {
            "symbol": normalized_symbol,
            "current_price": payload.get("c"),
            "change": payload.get("d"),
            "percent_change": payload.get("dp"),
            "high_price": payload.get("h"),
            "low_price": payload.get("l"),
            "open_price": payload.get("o"),
            "previous_close": payload.get("pc"),
            "timestamp": payload.get("t"),
        },
    )


def get_company_profile(symbol: str) -> ToolResult:
    """Get company fundamentals and identifying profile information."""
    tool = "get_company_profile"
    normalized_symbol, validation_error = _normalize_symbol(symbol, tool)
    if validation_error:
        return validation_error

    result = _request_finnhub(tool, "/stock/profile2", {"symbol": normalized_symbol})
    if not result["ok"]:
        return result

    payload = result["data"]
    if not isinstance(payload, dict) or not any(
        payload.get(field) for field in ("ticker", "name", "exchange", "country")
    ):
        return _error(
            tool,
            "PROFILE_NOT_FOUND",
            f"No company profile was found for symbol '{normalized_symbol}'.",
        )

    return _success(
        tool,
        {
            "symbol": normalized_symbol,
            "ticker": payload.get("ticker"),
            "company_name": payload.get("name"),
            "exchange": payload.get("exchange"),
            "industry": payload.get("finnhubIndustry"),
            "country": payload.get("country"),
            "currency": payload.get("currency"),
            "market_capitalization_millions": payload.get("marketCapitalization"),
            "shares_outstanding_millions": payload.get("shareOutstanding"),
            "ipo_date": payload.get("ipo"),
            "web_url": payload.get("weburl"),
            "logo_url": payload.get("logo"),
        },
    )


def get_company_news(symbol: str, days_back: int = 7) -> ToolResult:
    """Get recent company news for a ticker over a bounded date range."""
    tool = "get_company_news"
    normalized_symbol, validation_error = _normalize_symbol(symbol, tool)
    if validation_error:
        return validation_error

    if isinstance(days_back, bool) or not isinstance(days_back, int):
        return _error(tool, "INVALID_INPUT", "'days_back' must be an integer.")
    if not 1 <= days_back <= MAX_NEWS_LOOKBACK_DAYS:
        return _error(
            tool,
            "INVALID_INPUT",
            (
                "'days_back' must be between 1 and "
                f"{MAX_NEWS_LOOKBACK_DAYS}."
            ),
        )

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)
    result = _request_finnhub(
        tool,
        "/company-news",
        {
            "symbol": normalized_symbol,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
    )
    if not result["ok"]:
        return result

    payload = result["data"]
    if not isinstance(payload, list):
        return _error(
            tool,
            "INVALID_UPSTREAM_RESPONSE",
            "Finnhub returned an unexpected company-news response.",
        )
    if not payload:
        return _error(
            tool,
            "NEWS_NOT_FOUND",
            (
                f"No company news was found for '{normalized_symbol}' in the "
                f"last {days_back} days."
            ),
        )

    all_articles = [
        {
            "id": item.get("id"),
            "headline": item.get("headline"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "url": item.get("url"),
            "category": item.get("category"),
            "published_at": item.get("datetime"),
            "related_symbol": item.get("related"),
            "image_url": item.get("image"),
        }
        for item in payload
        if isinstance(item, dict) and item.get("headline")
    ]
    if not all_articles:
        return _error(
            tool,
            "NEWS_NOT_FOUND",
            (
                f"Finnhub returned no usable news articles for "
                f"'{normalized_symbol}'."
            ),
        )

    all_articles.sort(
        key=lambda article: article.get("published_at") or 0,
        reverse=True,
    )
    articles = all_articles[:MAX_NEWS_ARTICLES]
    return _success(
        tool,
        {
            "symbol": normalized_symbol,
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
            "days_back": days_back,
            "count": len(articles),
            "available_count": len(all_articles),
            "truncated": len(all_articles) > len(articles),
            "articles": articles,
        },
    )


TOOL_HANDLERS: Dict[str, Callable[..., ToolResult]] = {
    "search_symbol": search_symbol,
    "get_stock_quote": get_stock_quote,
    "get_company_profile": get_company_profile,
    "get_company_news": get_company_news,
}


def call_tool(tool_name: str, arguments: Mapping[str, Any]) -> ToolResult:
    """Dispatch a framework-provided MCP call to a registered tool handler."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return _error(
            tool_name,
            "UNKNOWN_TOOL",
            f"Unknown tool '{tool_name}'.",
        )
    if not isinstance(arguments, Mapping):
        return _error(
            tool_name,
            "INVALID_ARGUMENTS",
            "Tool arguments must be a JSON object.",
        )

    try:
        return handler(**dict(arguments))
    except TypeError:
        return _error(
            tool_name,
            "INVALID_ARGUMENTS",
            "The supplied arguments do not match the tool schema.",
        )
