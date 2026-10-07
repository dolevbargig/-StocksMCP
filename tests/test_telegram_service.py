"""Tests for Telegram transport, history, authorization, and concurrency."""

import asyncio
import json
import unittest
from typing import Any, Dict, List, Optional

import httpx

from chat_service import ChatResult
from telegram_service import (
    GENERIC_FAILURE_MESSAGE,
    MAX_HISTORY_TURNS,
    MAX_USER_MESSAGE_LENGTH,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramBotAPI,
    TelegramService,
    TelegramTransportError,
    TelegramUpdate,
    split_telegram_message,
)


def update(update_id: int, chat_id: int, text: Optional[str] = "Hello") -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        }
    )


class FakeTransport:
    def __init__(self) -> None:
        self.actions: List[tuple[int, str]] = []
        self.messages: List[tuple[int, str]] = []
        self.fail_messages = False

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        self.actions.append((chat_id, action))

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.fail_messages:
            raise TelegramTransportError("safe failure")
        self.messages.append((chat_id, text))


class FakeChatService:
    def __init__(self) -> None:
        self.calls: List[List[Dict[str, str]]] = []
        self.answer = "Answer"
        self.error: Optional[Exception] = None
        self.active = 0
        self.max_active = 0
        self.blocker: Optional[asyncio.Event] = None

    async def chat(self, messages: List[Dict[str, str]]) -> ChatResult:
        self.calls.append([dict(message) for message in messages])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.blocker is not None:
                await self.blocker.wait()
            if self.error is not None:
                raise self.error
            return ChatResult(answer=self.answer, trace=[])
        finally:
            self.active -= 1


class TelegramServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_allowlist_denies_without_model_or_reply(self) -> None:
        chat = FakeChatService()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset(),
        )

        result = await service.handle_update(update(1, 123))

        self.assertEqual(result.status, "unauthorized")
        self.assertEqual(chat.calls, [])
        self.assertEqual(transport.messages, [])

    async def test_follow_up_uses_only_that_chats_completed_history(self) -> None:
        chat = FakeChatService()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1, 2}),
        )

        await service.handle_update(update(1, 1, "First"))
        chat.answer = "Second answer"
        await service.handle_update(update(2, 1, "Follow up"))
        await service.handle_update(update(3, 2, "Other chat"))

        self.assertEqual(
            chat.calls[1],
            [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Follow up"},
            ],
        )
        self.assertEqual(
            chat.calls[2],
            [{"role": "user", "content": "Other chat"}],
        )

    async def test_duplicate_update_is_processed_once(self) -> None:
        chat = FakeChatService()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        first, second = await asyncio.gather(
            service.handle_update(update(99, 1)),
            service.handle_update(update(99, 1)),
        )

        self.assertEqual({first.status, second.status}, {"answered", "duplicate"})
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(len(transport.messages), 1)

    async def test_failed_turn_is_not_added_to_history(self) -> None:
        chat = FakeChatService()
        chat.error = RuntimeError("provider secret")
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        failed = await service.handle_update(update(1, 1, "First"))
        chat.error = None
        successful = await service.handle_update(update(2, 1, "Retry"))

        self.assertEqual(failed.status, "failed")
        self.assertEqual(successful.status, "answered")
        self.assertEqual(
            chat.calls[1],
            [{"role": "user", "content": "Retry"}],
        )
        self.assertEqual(transport.messages[0][1], GENERIC_FAILURE_MESSAGE)

    async def test_oversized_message_never_reaches_chat_service(self) -> None:
        chat = FakeChatService()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        result = await service.handle_update(
            update(1, 1, "x" * (MAX_USER_MESSAGE_LENGTH + 1))
        )

        self.assertEqual(result.status, "too_long")
        self.assertEqual(chat.calls, [])
        self.assertEqual(len(transport.messages), 1)

    async def test_history_evicts_old_complete_turns(self) -> None:
        chat = FakeChatService()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        for index in range(MAX_HISTORY_TURNS + 2):
            chat.answer = f"Answer {index}"
            await service.handle_update(update(index, 1, f"Question {index}"))

        last_call = chat.calls[-1]
        prior_users = [
            message["content"]
            for message in last_call[:-1]
            if message["role"] == "user"
        ]
        self.assertNotIn("Question 0", prior_users)
        self.assertIn("Question 1", prior_users)
        self.assertEqual(last_call[-1]["content"], f"Question {MAX_HISTORY_TURNS + 1}")

    async def test_same_chat_messages_are_serialized(self) -> None:
        chat = FakeChatService()
        chat.blocker = asyncio.Event()
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        first_task = asyncio.create_task(service.handle_update(update(1, 1, "First")))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(service.handle_update(update(2, 1, "Second")))
        await asyncio.sleep(0.01)
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.max_active, 1)

        chat.blocker.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(chat.max_active, 1)
        self.assertEqual(len(chat.calls), 2)

    async def test_long_answers_are_sent_in_order(self) -> None:
        chat = FakeChatService()
        chat.answer = "word " * 2_000
        transport = FakeTransport()
        service = TelegramService(
            chat_service=chat,
            transport=transport,
            allowed_chat_ids=frozenset({1}),
        )

        await service.handle_update(update(1, 1))

        chunks = [text for _chat_id, text in transport.messages]
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= TELEGRAM_MESSAGE_LIMIT for chunk in chunks))
        self.assertEqual("".join(chunks), chat.answer)


class TelegramSplitTest(unittest.TestCase):
    def test_split_preserves_every_character(self) -> None:
        text = ("alpha beta\n" * 900) + "tail"
        chunks = split_telegram_message(text, limit=97)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 97 for chunk in chunks))


class TelegramBotAPITest(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_uses_plain_json_without_parse_mode(self) -> None:
        captured: List[Dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        api = TelegramBotAPI(
            "fake-token",
            http_transport=httpx.MockTransport(handler),
        )
        await api.send_message(123, "Hello")

        self.assertEqual(captured, [{"chat_id": 123, "text": "Hello"}])
        self.assertNotIn("parse_mode", captured[0])

    async def test_provider_failure_is_sanitized(self) -> None:
        secret = "fake-token-secret-canary"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text=f"provider body {secret}")

        api = TelegramBotAPI(
            secret,
            http_transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(TelegramTransportError) as context:
            await api.send_message(123, "Hello")

        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn("provider body", str(context.exception))


if __name__ == "__main__":
    unittest.main()
