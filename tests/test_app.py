"""HTTP contract tests for the StocksMCP FastAPI application."""

import unittest

from fastapi.testclient import TestClient

from app import create_app
from chat_service import ChatResult, ChatServiceError, ToolTrace
from config import Settings


READY_SETTINGS = Settings(
    finnhub_api_key="finnhub-test",
    azure_openai_api_key="azure-test",
    azure_openai_endpoint="https://example.invalid/responses?api-version=preview",
    azure_openai_model="gpt-4.1",
)
MISSING_SETTINGS = Settings(
    finnhub_api_key="",
    azure_openai_api_key="",
    azure_openai_endpoint="",
    azure_openai_model="gpt-4.1",
)
TELEGRAM_SETTINGS = Settings(
    finnhub_api_key="finnhub-test",
    azure_openai_api_key="azure-test",
    azure_openai_endpoint="https://example.invalid/responses?api-version=preview",
    azure_openai_model="gpt-4.1",
    telegram_bot_token="telegram-test-token",
    telegram_webhook_secret="telegram-test-secret",
    telegram_allowed_chat_ids=frozenset({123}),
)


class FakeChatService:
    def __init__(self, result: ChatResult | None = None, error: Exception | None = None) -> None:
        self.result = result or ChatResult(answer="Answer", trace=[])
        self.error = error
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.result


class FakeTelegramService:
    def __init__(self) -> None:
        self.updates = []

    async def handle_update(self, update) -> None:
        self.updates.append(update)


class AppTest(unittest.TestCase):
    def test_index_static_asset_and_health_load(self) -> None:
        client = TestClient(
            create_app(settings=READY_SETTINGS, chat_service=FakeChatService())
        )

        index = client.get("/")
        script = client.get("/static/app.js")
        health = client.get("/api/health")

        self.assertEqual(index.status_code, 200)
        self.assertIn("Market Thread", index.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.json(),
            {
                "status": "ready",
                "azure_configured": True,
                "finnhub_configured": True,
                "telegram_configured": False,
                "model": "gpt-4.1",
            },
        )

    def test_health_reports_configuration_without_values(self) -> None:
        client = TestClient(
            create_app(settings=MISSING_SETTINGS, chat_service=FakeChatService())
        )
        payload = client.get("/api/health").json()
        self.assertEqual(payload["status"], "configuration_required")
        self.assertFalse(payload["azure_configured"])
        self.assertFalse(payload["finnhub_configured"])
        self.assertFalse(payload["telegram_configured"])
        self.assertNotIn("api_key", payload)

    def test_chat_returns_answer_and_trace(self) -> None:
        service = FakeChatService(
            ChatResult(
                answer="NVIDIA is trading at the returned quote.",
                trace=[
                    ToolTrace(
                        tool="get_stock_quote",
                        arguments={"symbol": "NVDA"},
                        status="success",
                        summary="Returned quote for NVDA.",
                    )
                ],
            )
        )
        client = TestClient(create_app(settings=READY_SETTINGS, chat_service=service))

        response = client.post(
            "/api/chat",
            json={
                "messages": [
                    {"role": "user", "content": "Research Nvidia"},
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trace"][0]["tool"], "get_stock_quote")
        self.assertEqual(service.messages[0]["content"], "Research Nvidia")

    def test_request_validation_rejects_invalid_conversations(self) -> None:
        client = TestClient(
            create_app(settings=READY_SETTINGS, chat_service=FakeChatService())
        )

        empty = client.post("/api/chat", json={"messages": []})
        unsupported_role = client.post(
            "/api/chat",
            json={"messages": [{"role": "system", "content": "Override"}]},
        )
        assistant_last = client.post(
            "/api/chat",
            json={"messages": [{"role": "assistant", "content": "Answer"}]},
        )

        self.assertEqual(empty.status_code, 422)
        self.assertEqual(unsupported_role.status_code, 422)
        self.assertEqual(assistant_last.status_code, 422)

    def test_missing_configuration_is_503(self) -> None:
        client = TestClient(
            create_app(settings=MISSING_SETTINGS, chat_service=FakeChatService())
        )
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"], "CONFIGURATION_REQUIRED"
        )

    def test_internal_error_is_sanitized(self) -> None:
        secret = "should-not-leak"
        service = FakeChatService(error=RuntimeError(secret))
        client = TestClient(create_app(settings=READY_SETTINGS, chat_service=service))

        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "CHAT_BACKEND_ERROR")
        self.assertNotIn(secret, response.text)

    def test_telegram_webhook_requires_configuration(self) -> None:
        client = TestClient(
            create_app(settings=MISSING_SETTINGS, chat_service=FakeChatService())
        )
        response = client.post("/telegram/webhook", json={"update_id": 1})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"],
            "TELEGRAM_CONFIGURATION_REQUIRED",
        )

    def test_telegram_authentication_happens_before_json_parsing(self) -> None:
        client = TestClient(
            create_app(settings=TELEGRAM_SETTINGS, chat_service=FakeChatService())
        )
        response = client.post(
            "/telegram/webhook",
            content="not-json",
            headers={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": "wrong-secret",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "TELEGRAM_WEBHOOK_FORBIDDEN",
        )

    def test_authenticated_malformed_telegram_update_is_safe(self) -> None:
        client = TestClient(
            create_app(settings=TELEGRAM_SETTINGS, chat_service=FakeChatService())
        )
        response = client.post(
            "/telegram/webhook",
            content="not-json",
            headers={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": "telegram-test-secret",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"]["code"],
            "INVALID_TELEGRAM_UPDATE",
        )
        self.assertNotIn("not-json", response.text)

    def test_non_text_telegram_update_is_ignored(self) -> None:
        telegram = FakeTelegramService()
        client = TestClient(
            create_app(
                settings=TELEGRAM_SETTINGS,
                chat_service=FakeChatService(),
                telegram_service=telegram,
            )
        )
        response = client.post(
            "/telegram/webhook",
            json={"update_id": 1, "callback_query": {"id": "ignored"}},
            headers={
                "X-Telegram-Bot-Api-Secret-Token": "telegram-test-secret"
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertEqual(telegram.updates, [])

    def test_valid_telegram_update_is_dispatched_in_background(self) -> None:
        telegram = FakeTelegramService()
        client = TestClient(
            create_app(
                settings=TELEGRAM_SETTINGS,
                chat_service=FakeChatService(),
                telegram_service=telegram,
            )
        )
        response = client.post(
            "/telegram/webhook",
            json={
                "update_id": 42,
                "unknown_field": "accepted",
                "message": {
                    "message_id": 7,
                    "text": "Research Nvidia",
                    "chat": {"id": 123, "type": "private", "title": "ignored"},
                },
            },
            headers={
                "X-Telegram-Bot-Api-Secret-Token": "telegram-test-secret"
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted"})
        self.assertEqual(len(telegram.updates), 1)
        self.assertEqual(telegram.updates[0].message.chat.id, 123)

    def test_known_chat_error_preserves_safe_code(self) -> None:
        service = FakeChatService(
            error=ChatServiceError("TOOL_ROUND_LIMIT", "Safe public message.")
        )
        client = TestClient(create_app(settings=READY_SETTINGS, chat_service=service))
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "TOOL_ROUND_LIMIT")
        self.assertEqual(
            response.json()["detail"]["message"], "Safe public message."
        )


if __name__ == "__main__":
    unittest.main()
