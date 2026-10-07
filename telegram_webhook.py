"""Credential-safe Telegram webhook administration CLI."""

import argparse
import sys
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import httpx

from config import ConfigurationError, Settings, load_settings


class TelegramAdminError(RuntimeError):
    """A sanitized Telegram administration failure."""


class TelegramAdminClient:
    def __init__(self, bot_token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                response = client.post(
                    f"{self._base_url}/{method}",
                    json=payload,
                )
            try:
                data = response.json()
            except ValueError:
                data = {}

            error_code = (
                data.get("error_code")
                if isinstance(data, dict) and isinstance(data.get("error_code"), int)
                else response.status_code
            )
            if not response.is_success:
                raise TelegramAdminError(
                    _telegram_rejection_message(method, error_code)
                )
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TelegramAdminError(
                    _telegram_rejection_message(method, error_code)
                )
            return data.get("result")
        except TelegramAdminError:
            raise
        except httpx.HTTPError:
            raise TelegramAdminError(
                "The backend could not contact Telegram."
            ) from None


def _telegram_rejection_message(method: str, error_code: int) -> str:
    if error_code in (401, 404):
        return (
            "Telegram did not accept TELEGRAM_BOT_TOKEN "
            f"(HTTP {error_code}). Verify the token in BotFather and .env."
        )
    if error_code == 409 and method == "getUpdates":
        return (
            "Telegram reports an active webhook. Run "
            "'telegram_webhook.py delete --drop-pending', send /start again, "
            "then retry discovery."
        )
    if error_code == 429:
        return "Telegram rate limited the request. Wait briefly and retry."
    return f"Telegram rejected the administration request (HTTP {error_code})."


def _require_bot_token(settings: Settings) -> None:
    if not settings.telegram_bot_token:
        raise ConfigurationError(
            "Missing required Telegram configuration: TELEGRAM_BOT_TOKEN"
        )


def _webhook_url(settings: Settings) -> str:
    public_url = settings.telegram_public_url.rstrip("/")
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigurationError(
            "TELEGRAM_PUBLIC_URL must be a public HTTPS base URL."
        )
    return f"{public_url}/telegram/webhook"


def discover_chat_ids(client: TelegramAdminClient) -> List[Tuple[int, str]]:
    result = client.call(
        "getUpdates",
        {"timeout": 0, "allowed_updates": ["message"]},
    )
    discovered = set()
    if isinstance(result, list):
        for update in result:
            if not isinstance(update, dict):
                continue
            message = update.get("message")
            chat = message.get("chat") if isinstance(message, dict) else None
            if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
                continue
            chat_type = chat.get("type")
            discovered.add(
                (
                    chat["id"],
                    chat_type if isinstance(chat_type, str) else "unknown",
                )
            )
    return sorted(discovered)


def set_webhook(client: TelegramAdminClient, settings: Settings) -> None:
    settings.require_telegram_configuration()
    if not settings.telegram_allowed_chat_ids:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_CHAT_IDS is empty. Discover and allow your chat before registration."
        )
    client.call(
        "setWebhook",
        {
            "url": _webhook_url(settings),
            "secret_token": settings.telegram_webhook_secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
    )


def webhook_info(client: TelegramAdminClient) -> Dict[str, Any]:
    result = client.call("getWebhookInfo", {})
    if not isinstance(result, dict):
        raise TelegramAdminError("Telegram returned invalid webhook information.")
    return {
        "url": result.get("url") if isinstance(result.get("url"), str) else "",
        "pending_update_count": (
            result.get("pending_update_count")
            if isinstance(result.get("pending_update_count"), int)
            else 0
        ),
        "has_custom_certificate": bool(result.get("has_custom_certificate")),
        "has_last_error": bool(result.get("last_error_message")),
    }


def delete_webhook(
    client: TelegramAdminClient,
    *,
    drop_pending_updates: bool,
) -> None:
    client.call(
        "deleteWebhook",
        {"drop_pending_updates": drop_pending_updates},
    )


def _print_chat_ids(chat_ids: Iterable[Tuple[int, str]]) -> None:
    found = False
    for chat_id, chat_type in chat_ids:
        found = True
        print(f"chat_id={chat_id} type={chat_type}")
    if not found:
        print("No chat IDs found. Send the bot a message, then try again.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the StocksMCP Telegram webhook using local .env settings."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover-chat-id")
    subparsers.add_parser("set")
    subparsers.add_parser("info")
    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument(
        "--drop-pending",
        action="store_true",
        help="Discard pending Telegram updates when deleting the webhook.",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        _require_bot_token(settings)
        client = TelegramAdminClient(settings.telegram_bot_token)

        if arguments.command == "discover-chat-id":
            _print_chat_ids(discover_chat_ids(client))
        elif arguments.command == "set":
            set_webhook(client, settings)
            print("Telegram webhook registered.")
        elif arguments.command == "info":
            info = webhook_info(client)
            print(f"url={info['url'] or '(not set)'}")
            print(f"pending_update_count={info['pending_update_count']}")
            print(f"has_custom_certificate={str(info['has_custom_certificate']).lower()}")
            print(f"has_last_error={str(info['has_last_error']).lower()}")
        elif arguments.command == "delete":
            delete_webhook(
                client,
                drop_pending_updates=arguments.drop_pending,
            )
            print("Telegram webhook deleted.")
        return 0
    except (ConfigurationError, TelegramAdminError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
