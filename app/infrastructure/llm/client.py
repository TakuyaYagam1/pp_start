"""OpenAI-compatible LLM client adapter for spam classification"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from app.config import Settings
from app.infrastructure.llm.prompt import build_spam_detection_messages


class LLMClientError(RuntimeError):
    """Controlled error for LLM request failures"""


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._chat_completions_url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or httpx.AsyncClient
        self._client: Any | None = None
        self._client_lock: asyncio.Lock | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    async def ask_is_spam(self, message_text: str) -> str:
        payload = {
            "model": self._model,
            "messages": build_spam_detection_messages(message_text),
            "temperature": 0,
            "max_tokens": 3,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.post(
                self._chat_completions_url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMClientError("LLM request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError("LLM request failed") from exc
        except ValueError as exc:
            raise LLMClientError("LLM response is not valid JSON") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("LLM response has unexpected format") from exc

        if not isinstance(content, str):
            raise LLMClientError("LLM response content is not text")

        return content.strip()

    async def aclose(self) -> None:
        if self._client is None:
            return

        client = self._client
        self._client = None
        close = getattr(client, "aclose", None)
        if callable(close):
            await close()

    async def _get_client(self) -> Any:
        if self._client is None:
            if self._client_lock is None:
                self._client_lock = asyncio.Lock()
            async with self._client_lock:
                if self._client is None:
                    self._client = self._client_factory(timeout=self._timeout_seconds)
        return self._client
