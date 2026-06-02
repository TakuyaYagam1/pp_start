from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from app.core.models import ActionMode, PendingVerification
from redis.asyncio import Redis


LLM_CACHE_TTL_SECONDS = 3 * 24 * 60 * 60
MIN_LLM_CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_LLM_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
ACTION_MODE_KEY = "settings:action_mode"


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)


class RedisClientLifecycle:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self.client: Redis | None = None

    async def startup(self) -> Redis:
        if self.client is None:
            self.client = create_redis_client(self._redis_url)
        await self.client.ping()
        return self.client

    async def shutdown(self) -> None:
        if self.client is None:
            return

        await self.client.aclose(close_connection_pool=True)
        self.client = None


@asynccontextmanager
async def redis_lifespan(redis_url: str) -> AsyncIterator[Redis]:
    lifecycle = RedisClientLifecycle(redis_url)
    client = await lifecycle.startup()
    try:
        yield client
    finally:
        await lifecycle.shutdown()


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
        await self._redis.set(
            self.key(chat_id, user_id),
            json.dumps(asdict(pending), ensure_ascii=False),
            ex=self._ttl_seconds,
        )
        return pending

    async def get(self, *, chat_id: int, user_id: int) -> PendingVerification | None:
        raw = await self._redis.get(self.key(chat_id, user_id))
        if raw is None:
            return None
        return PendingVerification.from_mapping(json.loads(raw))

    async def delete(self, *, chat_id: int, user_id: int) -> bool:
        deleted = await self._redis.delete(self.key(chat_id, user_id))
        return deleted > 0

    async def get_ttl(self, *, chat_id: int, user_id: int) -> int | None:
        ttl = await self._redis.ttl(self.key(chat_id, user_id))
        if ttl < 0:
            return None
        return ttl


class VerifiedUserRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"verified:{chat_id}:{user_id}"

    async def mark_verified(self, *, chat_id: int, user_id: int) -> None:
        await self._redis.set(self.key(chat_id, user_id), "1")

    async def is_verified(self, *, chat_id: int, user_id: int) -> bool:
        exists = await self._redis.exists(self.key(chat_id, user_id))
        return exists > 0


class BlacklistRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"blacklist:{chat_id}:{user_id}"

    async def add(self, *, chat_id: int, user_id: int) -> None:
        await self._redis.set(self.key(chat_id, user_id), "1")

    async def contains(self, *, chat_id: int, user_id: int) -> bool:
        exists = await self._redis.exists(self.key(chat_id, user_id))
        return exists > 0


class LLMResultCacheRepository:
    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int = LLM_CACHE_TTL_SECONDS,
    ) -> None:
        if not MIN_LLM_CACHE_TTL_SECONDS <= ttl_seconds <= MAX_LLM_CACHE_TTL_SECONDS:
            raise ValueError("LLM cache TTL must be from 1 to 7 days")

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
        return await self._redis.get(self.key(text))

    async def set(self, text: str, result: str) -> None:
        await self._redis.set(self.key(text), result, ex=self._ttl_seconds)

    async def get_ttl(self, text: str) -> int | None:
        ttl = await self._redis.ttl(self.key(text))
        if ttl < 0:
            return None
        return ttl


class RuntimeSettingsRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get_action_mode(self, *, default: ActionMode) -> ActionMode:
        raw = await self._redis.get(ACTION_MODE_KEY)
        if raw is None:
            return default

        try:
            return ActionMode(str(raw))
        except ValueError:
            await self._redis.delete(ACTION_MODE_KEY)
            return default

    async def set_action_mode(self, action_mode: ActionMode) -> None:
        await self._redis.set(ACTION_MODE_KEY, action_mode.value)

    async def reset_action_mode(self) -> None:
        await self._redis.delete(ACTION_MODE_KEY)
