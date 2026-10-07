"""Least-privilege environment tests for MCP subprocesses."""

import unittest

from mcp_client import _build_mcp_environment


class MCPClientEnvironmentTest(unittest.TestCase):
    def test_only_allowlisted_runtime_values_and_finnhub_key_are_forwarded(self) -> None:
        environment = _build_mcp_environment(
            "finnhub-child-key",
            {
                "PATH": "/safe/path",
                "HOME": "/safe/home",
                "LANG": "en_US.UTF-8",
                "AZURE_OPENAI_API_KEY": "azure-secret-canary",
                "TELEGRAM_BOT_TOKEN": "telegram-token-canary",
                "TELEGRAM_WEBHOOK_SECRET": "telegram-secret-canary",
                "UNRELATED_SECRET": "unrelated-canary",
            },
        )

        self.assertEqual(environment["FINNHUB_API_KEY"], "finnhub-child-key")
        self.assertEqual(environment["PATH"], "/safe/path")
        self.assertEqual(environment["HOME"], "/safe/home")
        self.assertNotIn("AZURE_OPENAI_API_KEY", environment)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", environment)
        self.assertNotIn("TELEGRAM_WEBHOOK_SECRET", environment)
        self.assertNotIn("UNRELATED_SECRET", environment)


if __name__ == "__main__":
    unittest.main()
