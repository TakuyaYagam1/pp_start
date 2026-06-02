from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class RedisMiddleware(BaseMiddleware):
    def __init__(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.setdefault("redis_client", self._redis_client)
        return await handler(event, data)


__all__ = ("RedisMiddleware",)
