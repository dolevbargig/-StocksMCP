"""Configuration tests for browser and Telegram settings."""

import unittest

from config import ConfigurationError, load_settings


BASE_ENV = {
    "FINNHUB_API_KEY": "finnhub",
    "AZURE_OPENAI_API_KEY": "azure",
    "AZURE_OPENAI_ENDPOINT": "https://example.invalid/responses",
    "AZURE_OPENAI_MODEL": "gpt-4.1",
}


class ConfigTest(unittest.TestCase):
    def test_telegram_settings_and_chat_ids_are_parsed(self) -> None:
        settings = load_settings(
            env_file=None,
            environ={
                **BASE_ENV,
                "TELEGRAM_BOT_TOKEN": " bot-token ",
                "TELEGRAM_WEBHOOK_SECRET": " webhook-secret ",
                "TELEGRAM_ALLOWED_CHAT_IDS": "123, -456, 123",
                "TELEGRAM_PUBLIC_URL": " https://example.ngrok.app ",
            },
        )
        self.assertTrue(settings.telegram_configured)
        self.assertEqual(settings.telegram_allowed_chat_ids, frozenset({123, -456}))
        self.assertEqual(settings.telegram_public_url, "https://example.ngrok.app")
        self.assertTrue(settings.ready)

    def test_empty_allowlist_denies_by_default_without_affecting_browser(self) -> None:
        settings = load_settings(env_file=None, environ=BASE_ENV)
        self.assertEqual(settings.telegram_allowed_chat_ids, frozenset())
        self.assertFalse(settings.telegram_configured)
        self.assertTrue(settings.ready)

    def test_malformed_chat_ids_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings(
                env_file=None,
                environ={**BASE_ENV, "TELEGRAM_ALLOWED_CHAT_IDS": "123,abc"},
            )
        with self.assertRaises(ConfigurationError):
            load_settings(
                env_file=None,
                environ={**BASE_ENV, "TELEGRAM_ALLOWED_CHAT_IDS": "123,,456"},
            )

    def test_telegram_requirement_names_missing_fields_only(self) -> None:
        settings = load_settings(env_file=None, environ=BASE_ENV)
        with self.assertRaises(ConfigurationError) as context:
            settings.require_telegram_configuration()
        message = str(context.exception)
        self.assertIn("TELEGRAM_BOT_TOKEN", message)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", message)


if __name__ == "__main__":
    unittest.main()
