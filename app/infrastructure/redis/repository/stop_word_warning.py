"""Redis adapter for stop-word warning state"""

from __future__ import annotations

from redis.asyncio import Redis

from app.infrastructure.redis.client import redis_value_to_str

STOP_WORD_WARNING_KEY_PREFIX = "stop_word_warning"


class StopWordWarningRepository:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"{STOP_WORD_WARNING_KEY_PREFIX}:{chat_id}:{user_id}"

    async def get_warned_term(self, *, chat_id: int, user_id: int) -> str | None:
        raw = await self._redis.get(self.key(chat_id, user_id))
        return redis_value_to_str(raw)

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        matched_term: str,
    ) -> bool:
        was_set = await self._redis.set(
            self.key(chat_id, user_id),
            matched_term,
            ex=self._ttl_seconds,
            nx=True,
        )
        return bool(was_set)

    async def clear(self, *, chat_id: int, user_id: int) -> None:
        await self._redis.delete(self.key(chat_id, user_id))
