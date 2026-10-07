"""Minimal Azure OpenAI Responses API transport."""

import asyncio
from typing import Any, Callable, Dict, Tuple

import requests


REQUEST_TIMEOUT_SECONDS: Tuple[int, int] = (10, 90)


class AzureResponsesError(RuntimeError):
    """A sanitized Azure Responses API failure."""

    def __init__(self, code: str, public_message: str, *, retryable: bool) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class AzureResponsesClient:
    """Post Responses API payloads to an exact Azure endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        request: Callable[..., requests.Response] = requests.post,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._request = request

    async def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._create_sync, payload)

    def _create_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = self._request(
                self._endpoint,
                headers={
                    "api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as error:
            raise AzureResponsesError(
                "AZURE_TIMEOUT",
                "The language model did not respond before the timeout.",
                retryable=True,
            ) from error
        except requests.ConnectionError as error:
            raise AzureResponsesError(
                "AZURE_CONNECTION_ERROR",
                "The backend could not connect to the language model.",
                retryable=True,
            ) from error
        except requests.RequestException as error:
            raise AzureResponsesError(
                "AZURE_REQUEST_ERROR",
                "The language model request could not be completed.",
                retryable=True,
            ) from error

        if response.status_code in (401, 403):
            raise AzureResponsesError(
                "AZURE_AUTHENTICATION_ERROR",
                "The language model credentials were rejected.",
                retryable=False,
            )
        if response.status_code == 429:
            raise AzureResponsesError(
                "AZURE_RATE_LIMITED",
                "The language model is temporarily rate limited. Try again later.",
                retryable=True,
            )
        if not response.ok:
            raise AzureResponsesError(
                "AZURE_HTTP_ERROR",
                f"The language model returned HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
            )

        try:
            data = response.json()
        except ValueError as error:
            raise AzureResponsesError(
                "AZURE_INVALID_RESPONSE",
                "The language model returned an invalid response.",
                retryable=True,
            ) from error

        if not isinstance(data, dict) or not isinstance(data.get("output"), list):
            raise AzureResponsesError(
                "AZURE_INVALID_RESPONSE",
                "The language model returned an unexpected response shape.",
                retryable=True,
            )
        return data
