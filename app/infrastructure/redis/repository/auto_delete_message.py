"""Redis adapter for pending Telegram message auto-delete jobs"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil

from redis.asyncio import Redis

from app.domain import AutoDeleteMessage
from app.infrastructure.redis.client import (
    delete_redis_key,
    list_redis_json_models,
    redis_ttl_or_none,
    set_redis_json_model,
)

AUTO_DELETE_MESSAGE_KEY_PREFIX = "auto_delete_message"


class AutoDeleteMessageRepository:
    def __init__(self, redis: Redis, *, cleanup_grace_seconds: int) -> None:
        self._redis = redis
        self._cleanup_grace_seconds = cleanup_grace_seconds

    @staticmethod
    def key(chat_id: int, message_id: int) -> str:
        return f"{AUTO_DELETE_MESSAGE_KEY_PREFIX}:{chat_id}:{message_id}"

    @staticmethod
    def pattern() -> str:
        return f"{AUTO_DELETE_MESSAGE_KEY_PREFIX}:*"

    async def create(
        self,
        *,
        chat_id: int,
        message_id: int,
        delete_at: datetime,
        user_id: int | None = None,
    ) -> AutoDeleteMessage:
        normalized_delete_at = delete_at.astimezone(UTC)
        pending = AutoDeleteMessage(
            chat_id=chat_id,
            message_id=message_id,
            delete_at=normalized_delete_at.isoformat(),
            user_id=user_id,
        )
        ttl_seconds = self._ttl_seconds(normalized_delete_at)
        await set_redis_json_model(
            self._redis,
            self.key(chat_id, message_id),
            pending,
            ex=ttl_seconds,
        )
        return pending

    async def list(self) -> tuple[AutoDeleteMessage, ...]:
        return await list_redis_json_models(
            self._redis,
            self.pattern(),
            AutoDeleteMessage.from_mapping,
        )

    async def delete(self, *, chat_id: int, message_id: int) -> bool:
        return await delete_redis_key(self._redis, self.key(chat_id, message_id))

    async def get_ttl(self, *, chat_id: int, message_id: int) -> int | None:
        return await redis_ttl_or_none(self._redis, self.key(chat_id, message_id))

    def _ttl_seconds(self, delete_at: datetime) -> int:
        delay_seconds = max(0.0, (delete_at - datetime.now(UTC)).total_seconds())
        return max(1, ceil(delay_seconds + self._cleanup_grace_seconds))
