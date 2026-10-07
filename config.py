"""Environment-backed configuration for the StocksMCP application."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Mapping, Optional

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when required server-side configuration is missing."""


@dataclass(frozen=True)
class Settings:
    """Immutable application settings loaded from server-side environment variables."""

    finnhub_api_key: str
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_model: str
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_allowed_chat_ids: FrozenSet[int] = frozenset()
    telegram_public_url: str = ""

    @property
    def finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)

    @property
    def azure_configured(self) -> bool:
        return bool(
            self.azure_openai_api_key
            and self.azure_openai_endpoint
            and self.azure_openai_model
        )

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_webhook_secret)

    @property
    def ready(self) -> bool:
        return self.finnhub_configured and self.azure_configured

    def require_chat_configuration(self) -> None:
        missing = []
        if not self.finnhub_api_key:
            missing.append("FINNHUB_API_KEY")
        if not self.azure_openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not self.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.azure_openai_model:
            missing.append("AZURE_OPENAI_MODEL")
        if missing:
            raise ConfigurationError(
                "Missing required server configuration: " + ", ".join(missing)
            )

    def require_telegram_configuration(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_webhook_secret:
            missing.append("TELEGRAM_WEBHOOK_SECRET")
        if missing:
            raise ConfigurationError(
                "Missing required Telegram configuration: " + ", ".join(missing)
            )


def _parse_chat_ids(raw_value: str) -> FrozenSet[int]:
    if not raw_value.strip():
        return frozenset()

    parts = [part.strip() for part in raw_value.split(",")]
    if any(not part for part in parts):
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_CHAT_IDS must be a comma-separated list of integers."
        )
    try:
        return frozenset(int(part) for part in parts)
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_CHAT_IDS must be a comma-separated list of integers."
        ) from error


def load_settings(
    *,
    env_file: Optional[Path] = DEFAULT_ENV_FILE,
    environ: Optional[Mapping[str, str]] = None,
) -> Settings:
    """Load settings without overriding variables already set by the process."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)

    source = os.environ if environ is None else environ
    return Settings(
        finnhub_api_key=source.get("FINNHUB_API_KEY", "").strip(),
        azure_openai_api_key=source.get("AZURE_OPENAI_API_KEY", "").strip(),
        azure_openai_endpoint=source.get("AZURE_OPENAI_ENDPOINT", "").strip(),
        azure_openai_model=source.get("AZURE_OPENAI_MODEL", "").strip(),
        telegram_bot_token=source.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_webhook_secret=source.get("TELEGRAM_WEBHOOK_SECRET", "").strip(),
        telegram_allowed_chat_ids=_parse_chat_ids(
            source.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        ),
        telegram_public_url=source.get("TELEGRAM_PUBLIC_URL", "").strip(),
    )
