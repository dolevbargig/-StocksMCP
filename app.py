"""FastAPI application for the StocksMCP browser chat experience."""

import secrets
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from azure_responses import AzureResponsesError
from chat_service import ChatResult, ChatService, ChatServiceError
from config import ConfigurationError, Settings, load_settings
from telegram_service import TelegramBotAPI, TelegramService, TelegramUpdate


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "static"
MAX_TOTAL_MESSAGE_CHARACTERS = 20_000


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: List[ChatMessage] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_conversation(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("The final message must have the user role.")
        total_characters = sum(len(message.content) for message in self.messages)
        if total_characters > MAX_TOTAL_MESSAGE_CHARACTERS:
            raise ValueError("The conversation is too long for one request.")
        return self


class ToolTraceResponse(BaseModel):
    tool: str
    arguments: Dict[str, Any]
    status: Literal["success", "error"]
    summary: str


class ChatResponse(BaseModel):
    answer: str
    trace: List[ToolTraceResponse]


class HealthResponse(BaseModel):
    status: Literal["ready", "configuration_required"]
    azure_configured: bool
    finnhub_configured: bool
    telegram_configured: bool
    model: Optional[str]


def create_app(
    *,
    settings: Optional[Settings] = None,
    chat_service: Optional[Any] = None,
    telegram_service: Optional[Any] = None,
) -> FastAPI:
    application_settings = settings or load_settings()
    service = chat_service or ChatService(application_settings)
    telegram = telegram_service or TelegramService(
        chat_service=service,
        transport=TelegramBotAPI(application_settings.telegram_bot_token),
        allowed_chat_ids=application_settings.telegram_allowed_chat_ids,
    )

    application = FastAPI(
        title="StocksMCP Chat",
        description="Grounded US-stock research through Azure OpenAI and MCP.",
        version="1.0.0",
    )
    application.state.settings = application_settings
    application.state.chat_service = service
    application.state.telegram_service = telegram
    application.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ready" if application_settings.ready else "configuration_required",
            azure_configured=application_settings.azure_configured,
            finnhub_configured=application_settings.finnhub_configured,
            telegram_configured=application_settings.telegram_configured,
            model=(
                application_settings.azure_openai_model
                if application_settings.azure_openai_model
                else None
            ),
        )

    @application.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        try:
            application_settings.require_chat_configuration()
            result: ChatResult = await service.chat(
                [message.model_dump() for message in request.messages]
            )
            return ChatResponse(**result.to_dict())
        except ConfigurationError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "CONFIGURATION_REQUIRED",
                    "message": str(error),
                },
            ) from error
        except AzureResponsesError as error:
            raise HTTPException(
                status_code=502,
                detail={"code": error.code, "message": error.public_message},
            ) from error
        except ChatServiceError as error:
            raise HTTPException(
                status_code=502,
                detail={"code": error.code, "message": error.public_message},
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "CHAT_BACKEND_ERROR",
                    "message": "The research request could not be completed.",
                },
            ) from error

    @application.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> Dict[str, str]:
        try:
            application_settings.require_telegram_configuration()
        except ConfigurationError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "TELEGRAM_CONFIGURATION_REQUIRED",
                    "message": str(error),
                },
            ) from error

        supplied_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )
        if not supplied_secret or not secrets.compare_digest(
            supplied_secret,
            application_settings.telegram_webhook_secret,
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "TELEGRAM_WEBHOOK_FORBIDDEN"},
            )

        try:
            payload = await request.json()
            update = TelegramUpdate.model_validate(payload)
        except (ValueError, ValidationError) as error:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_TELEGRAM_UPDATE"},
            ) from error

        message = update.message
        if message is None or not isinstance(message.text, str) or not message.text.strip():
            return {"status": "ignored"}

        background_tasks.add_task(telegram.handle_update, update)
        return {"status": "accepted"}

    return application


app = create_app()
