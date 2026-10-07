"""Telegram webhook models, Bot API transport, and bounded chat coordination."""

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, FrozenSet, List, Optional, Protocol, Tuple

import httpx
from pydantic import BaseModel, ConfigDict

from chat_service import ChatResult


TELEGRAM_MESSAGE_LIMIT = 4_096
MAX_USER_MESSAGE_LENGTH = 4_000
MAX_HISTORY_TURNS = 10
MAX_HISTORY_CHARACTERS = 20_000
MAX_TRACKED_UPDATES = 1_000
MAX_CONCURRENT_CHATS = 3
GENERIC_FAILURE_MESSAGE = (
    "I could not complete that research request. Please try again in a moment."
)
OVERSIZED_MESSAGE = "Please keep each research question under 4,000 characters."


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    type: Optional[str] = None


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int
    chat: TelegramChat
    text: Optional[str] = None


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: Optional[TelegramMessage] = None


class TelegramTransportError(RuntimeError):
    """A provider-safe Telegram Bot API failure."""


class TelegramTransport(Protocol):
    async def send_chat_action(self, chat_id: int, action: str) -> None: ...

    async def send_message(self, chat_id: int, text: str) -> None: ...


class TelegramBotAPI:
    """Minimal Telegram Bot API client with sanitized failures."""

    def __init__(
        self,
        bot_token: str,
        *,
        http_transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._http_transport = http_transport

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        await self._call(
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
        )

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text},
        )

    async def _call(self, method: str, payload: Dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=httpx.Timeout(15.0),
            ) as client:
                response = await client.post(
                    f"{self._base_url}/{method}",
                    json=payload,
                )
            if not response.is_success:
                raise TelegramTransportError(
                    "Telegram rejected an outbound bot request."
                )
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TelegramTransportError(
                    "Telegram returned an invalid bot response."
                )
        except TelegramTransportError:
            raise
        except (httpx.HTTPError, ValueError):
            raise TelegramTransportError(
                "The backend could not contact Telegram."
            ) from None


class TelegramChatService(Protocol):
    async def chat(self, messages: List[Dict[str, str]]) -> ChatResult: ...


@dataclass(frozen=True)
class TelegramHandleResult:
    status: str


class TelegramService:
    """Coordinate authorized Telegram updates with the existing ChatService."""

    def __init__(
        self,
        *,
        chat_service: TelegramChatService,
        transport: TelegramTransport,
        allowed_chat_ids: FrozenSet[int],
        max_concurrent_chats: int = MAX_CONCURRENT_CHATS,
    ) -> None:
        self._chat_service = chat_service
        self._transport = transport
        self._allowed_chat_ids = allowed_chat_ids
        self._histories: Dict[int, Deque[Tuple[str, str]]] = {}
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._update_ids: "OrderedDict[int, None]" = OrderedDict()
        self._update_lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(max_concurrent_chats)

    async def handle_update(self, update: TelegramUpdate) -> TelegramHandleResult:
        if not await self._claim_update(update.update_id):
            return TelegramHandleResult(status="duplicate")

        message = update.message
        if message is None or not isinstance(message.text, str) or not message.text.strip():
            return TelegramHandleResult(status="ignored")

        chat_id = message.chat.id
        if chat_id not in self._allowed_chat_ids:
            return TelegramHandleResult(status="unauthorized")

        text = message.text.strip()
        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if len(text) > MAX_USER_MESSAGE_LENGTH:
                await self._send_safely(chat_id, OVERSIZED_MESSAGE)
                return TelegramHandleResult(status="too_long")

            try:
                try:
                    await self._transport.send_chat_action(chat_id, "typing")
                except Exception:
                    pass

                messages = self._history_messages(chat_id)
                messages.append({"role": "user", "content": text})
                async with self._concurrency:
                    result = await self._chat_service.chat(messages)

                chunks = split_telegram_message(result.answer)
                for chunk in chunks:
                    await self._transport.send_message(chat_id, chunk)
                self._commit_turn(chat_id, text, result.answer)
                return TelegramHandleResult(status="answered")
            except Exception:
                await self._send_safely(chat_id, GENERIC_FAILURE_MESSAGE)
                return TelegramHandleResult(status="failed")

    async def _claim_update(self, update_id: int) -> bool:
        async with self._update_lock:
            if update_id in self._update_ids:
                return False
            self._update_ids[update_id] = None
            while len(self._update_ids) > MAX_TRACKED_UPDATES:
                self._update_ids.popitem(last=False)
            return True

    def _history_messages(self, chat_id: int) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        history = self._histories.get(chat_id)
        if history is None:
            return messages
        for user_text, assistant_text in history:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        return messages

    def _commit_turn(self, chat_id: int, user_text: str, assistant_text: str) -> None:
        history = self._histories.setdefault(chat_id, deque())
        history.append((user_text, assistant_text))

        def character_count() -> int:
            return sum(len(user) + len(assistant) for user, assistant in history)

        while history and (
            len(history) > MAX_HISTORY_TURNS
            or character_count() > MAX_HISTORY_CHARACTERS
        ):
            history.popleft()
        if not history:
            self._histories.pop(chat_id, None)

    async def _send_safely(self, chat_id: int, text: str) -> None:
        try:
            for chunk in split_telegram_message(text):
                await self._transport.send_message(chat_id, chunk)
        except Exception:
            pass


def split_telegram_message(
    text: str,
    *,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> List[str]:
    """Split text without losing characters, preferring natural boundaries."""
    if limit < 1:
        raise ValueError("Telegram message limit must be positive.")
    if not text:
        return ["No response was generated."]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < 1:
            split_at = limit
        else:
            split_at += 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks
