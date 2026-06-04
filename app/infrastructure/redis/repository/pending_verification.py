"""Redis adapter for pending verification records with TTL"""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis

from app.domain import PendingVerification
from app.infrastructure.redis.client import (
    delete_redis_key,
    get_redis_json_model,
    redis_ttl_or_none,
    set_redis_json_model,
)


class PendingVerificationRepository:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"verify:{chat_id}:{user_id}"

    async def create(
        self,
        *,
        user_id: int,
        chat_id: int,
        verification_message_id: int,
        message_thread_id: int | None = None,
        verification_chat_id: int | None = None,
    ) -> PendingVerification:
        pending = PendingVerification(
            user_id=user_id,
            chat_id=chat_id,
            verification_message_id=verification_message_id,
            created_at=datetime.now(UTC).isoformat(),
            message_thread_id=message_thread_id,
            verification_chat_id=verification_chat_id,
        )
        await set_redis_json_model(
            self._redis,
            self.key(chat_id, user_id),
            pending,
            ex=self._ttl_seconds,
        )
        return pending

    async def get(self, *, chat_id: int, user_id: int) -> PendingVerification | None:
        key = self.key(chat_id, user_id)
        return await get_redis_json_model(
            self._redis,
            key,
            PendingVerification.from_mapping,
        )

    async def delete(self, *, chat_id: int, user_id: int) -> bool:
        return await delete_redis_key(self._redis, self.key(chat_id, user_id))

    async def get_ttl(self, *, chat_id: int, user_id: int) -> int | None:
        return await redis_ttl_or_none(self._redis, self.key(chat_id, user_id))
