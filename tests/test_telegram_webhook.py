"""Tests for credential-safe Telegram webhook administration helpers."""

import unittest
from typing import Any, Dict, List, Tuple

from config import ConfigurationError, Settings
from telegram_webhook import (
    _telegram_rejection_message,
    discover_chat_ids,
    set_webhook,
    webhook_info,
)


class FakeAdminClient:
    def __init__(self, results: Dict[str, Any]) -> None:
        self.results = results
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        self.calls.append((method, payload))
        return self.results.get(method)


def settings(*, allowed=frozenset({123})) -> Settings:
    return Settings(
        finnhub_api_key="finnhub",
        azure_openai_api_key="azure",
        azure_openai_endpoint="https://example.invalid/responses",
        azure_openai_model="gpt-4.1",
        telegram_bot_token="bot-token",
        telegram_webhook_secret="webhook-secret",
        telegram_allowed_chat_ids=allowed,
        telegram_public_url="https://example.ngrok.app/",
    )


class TelegramWebhookHelperTest(unittest.TestCase):
    def test_rejection_messages_are_actionable_without_provider_content(self) -> None:
        auth = _telegram_rejection_message("getUpdates", 401)
        conflict = _telegram_rejection_message("getUpdates", 409)
        limited = _telegram_rejection_message("getUpdates", 429)
        self.assertIn("TELEGRAM_BOT_TOKEN", auth)
        self.assertIn("active webhook", conflict)
        self.assertIn("rate limited", limited)

    def test_discover_chat_ids_returns_only_safe_identity_fields(self) -> None:
        client = FakeAdminClient(
            {
                "getUpdates": [
                    {
                        "update_id": 1,
                        "message": {
                            "text": "private message text",
                            "chat": {"id": 123, "type": "private", "first_name": "Name"},
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {"chat": {"id": -456, "type": "group"}},
                    },
                ]
            }
        )
        self.assertEqual(
            discover_chat_ids(client),
            [(-456, "group"), (123, "private")],
        )

    def test_set_webhook_uses_secret_and_normalized_url(self) -> None:
        client = FakeAdminClient({"setWebhook": True})
        set_webhook(client, settings())
        method, payload = client.calls[0]
        self.assertEqual(method, "setWebhook")
        self.assertEqual(
            payload["url"],
            "https://example.ngrok.app/telegram/webhook",
        )
        self.assertEqual(payload["secret_token"], "webhook-secret")
        self.assertEqual(payload["allowed_updates"], ["message"])

    def test_set_webhook_refuses_empty_allowlist(self) -> None:
        client = FakeAdminClient({"setWebhook": True})
        with self.assertRaises(ConfigurationError):
            set_webhook(client, settings(allowed=frozenset()))
        self.assertEqual(client.calls, [])

    def test_info_reduces_provider_data_to_safe_status(self) -> None:
        client = FakeAdminClient(
            {
                "getWebhookInfo": {
                    "url": "https://example.ngrok.app/telegram/webhook",
                    "pending_update_count": 2,
                    "last_error_message": "provider detail not returned",
                    "secret_token": "must-not-surface",
                }
            }
        )
        result = webhook_info(client)
        self.assertEqual(result["pending_update_count"], 2)
        self.assertTrue(result["has_last_error"])
        self.assertNotIn("last_error_message", result)
        self.assertNotIn("secret_token", result)


if __name__ == "__main__":
    unittest.main()
