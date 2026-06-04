"""Redis adapter for cached LLM spam decisions"""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from app.infrastructure.redis.client import redis_ttl_or_none, redis_value_to_str

LLM_CACHE_TTL_SECONDS = 5 * 60
MIN_LLM_CACHE_TTL_SECONDS = 60
MAX_LLM_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


class LLMResultCacheRepository:
    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int = LLM_CACHE_TTL_SECONDS,
    ) -> None:
        if not MIN_LLM_CACHE_TTL_SECONDS <= ttl_seconds <= MAX_LLM_CACHE_TTL_SECONDS:
            raise ValueError("LLM cache TTL must be from 60 seconds to 7 days")

        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def normalize_text(text: str) -> str:
        return " ".join(text.casefold().split())

    @classmethod
    def key(cls, text: str) -> str:
        normalized_text = cls.normalize_text(text)
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        return f"llm:{digest}"

    async def get(self, text: str) -> str | None:
        raw = await self._redis.get(self.key(text))
        return redis_value_to_str(raw)

    async def set(self, text: str, result: str) -> None:
        await self._redis.set(self.key(text), result, ex=self._ttl_seconds)

    async def get_ttl(self, text: str) -> int | None:
        return await redis_ttl_or_none(self._redis, self.key(text))
