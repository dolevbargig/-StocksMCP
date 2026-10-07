"""Unit tests for bounded, structured Finnhub tool results."""

import os
import unittest
from unittest.mock import Mock, patch

import requests

import tool_handlers


class ToolHandlerTest(unittest.TestCase):
    def test_search_results_are_bounded(self) -> None:
        provider_results = [
            {
                "symbol": f"TEST{index}",
                "displaySymbol": f"TEST{index}",
                "description": f"Company {index}",
                "type": "Common Stock",
            }
            for index in range(12)
        ]
        with patch(
            "tool_handlers._request_finnhub",
            return_value={
                "ok": True,
                "tool": "search_symbol",
                "data": {"result": provider_results},
            },
        ):
            result = tool_handlers.search_symbol("Test")

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["count"], tool_handlers.MAX_SEARCH_RESULTS)
        self.assertEqual(data["available_count"], 12)
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["candidates"]), tool_handlers.MAX_SEARCH_RESULTS)

    def test_news_results_are_recent_first_and_bounded(self) -> None:
        provider_results = [
            {
                "id": index,
                "headline": f"Headline {index}",
                "summary": "Summary",
                "source": "Source",
                "datetime": index,
            }
            for index in range(15)
        ]
        with patch(
            "tool_handlers._request_finnhub",
            return_value={
                "ok": True,
                "tool": "get_company_news",
                "data": provider_results,
            },
        ):
            result = tool_handlers.get_company_news("nvda")

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["count"], tool_handlers.MAX_NEWS_ARTICLES)
        self.assertEqual(data["available_count"], 15)
        self.assertTrue(data["truncated"])
        self.assertEqual(data["articles"][0]["published_at"], 14)
        self.assertEqual(data["articles"][-1]["published_at"], 5)

    def test_validation_errors_do_not_call_finnhub(self) -> None:
        with patch("tool_handlers.requests.get") as request:
            symbol_result = tool_handlers.get_stock_quote("bad symbol!")
            days_result = tool_handlers.get_company_news("NVDA", True)
        request.assert_not_called()
        self.assertEqual(symbol_result["error"]["code"], "INVALID_SYMBOL")
        self.assertEqual(days_result["error"]["code"], "INVALID_INPUT")

    def test_missing_api_key_is_structured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = tool_handlers.get_stock_quote("NVDA")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "CONFIGURATION_ERROR")

    def test_rate_limit_and_timeout_are_retryable(self) -> None:
        rate_response = Mock(status_code=429, ok=False)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test"}, clear=True):
            with patch("tool_handlers.requests.get", return_value=rate_response):
                rate_result = tool_handlers.search_symbol("Nvidia")
            with patch(
                "tool_handlers.requests.get",
                side_effect=requests.Timeout,
            ):
                timeout_result = tool_handlers.search_symbol("Nvidia")

        self.assertEqual(rate_result["error"]["code"], "RATE_LIMITED")
        self.assertTrue(rate_result["error"]["retryable"])
        self.assertEqual(timeout_result["error"]["code"], "UPSTREAM_TIMEOUT")
        self.assertTrue(timeout_result["error"]["retryable"])

    def test_invalid_json_and_empty_payloads_are_explicit(self) -> None:
        invalid_json_response = Mock(status_code=200, ok=True)
        invalid_json_response.json.side_effect = ValueError
        empty_quote_response = Mock(status_code=200, ok=True)
        empty_quote_response.json.return_value = {}

        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test"}, clear=True):
            with patch(
                "tool_handlers.requests.get",
                return_value=invalid_json_response,
            ):
                invalid_json = tool_handlers.get_stock_quote("NVDA")
            with patch(
                "tool_handlers.requests.get",
                return_value=empty_quote_response,
            ):
                empty_quote = tool_handlers.get_stock_quote("NVDA")

        self.assertEqual(
            invalid_json["error"]["code"], "INVALID_UPSTREAM_RESPONSE"
        )
        self.assertEqual(empty_quote["error"]["code"], "QUOTE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
