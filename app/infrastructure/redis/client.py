"""Redis client factory and lifecycle helpers"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from typing import Any, TypeVar

from redis.asyncio import Redis

T = TypeVar("T")
REDIS_JSON_DECODE_ERRORS = (
    json.JSONDecodeError,
    KeyError,
    TypeError,
    ValueError,
)


def redis_value_to_str(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def redis_json_dumps(value: Any) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise TypeError("Redis JSON value must be a dataclass instance or mapping")

    return json.dumps(payload, ensure_ascii=False)


async def set_redis_json_model(
    redis: Redis,
    key: bytes | str,
    value: Any,
    *,
    ex: int,
) -> None:
    await redis.set(key, redis_json_dumps(value), ex=ex)


async def get_redis_json_model(
    redis: Redis,
    key: bytes | str,
    factory: Callable[[dict[str, Any]], T],
) -> T | None:
    raw = await redis.get(key)
    raw_text = redis_value_to_str(raw)
    if raw_text is None:
        return None

    try:
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise TypeError("Redis JSON payload is not an object")
        return factory(payload)
    except REDIS_JSON_DECODE_ERRORS:
        await redis.delete(key)
        return None


async def list_redis_json_models(
    redis: Redis,
    pattern: bytes | str,
    factory: Callable[[dict[str, Any]], T],
) -> tuple[T, ...]:
    models: list[T] = []
    async for key in redis.scan_iter(match=pattern):
        model = await get_redis_json_model(redis, key, factory)
        if model is not None:
            models.append(model)
    return tuple(models)


async def delete_redis_key(redis: Redis, key: bytes | str) -> bool:
    deleted = await redis.delete(key)
    return deleted > 0


async def redis_ttl_or_none(redis: Redis, key: bytes | str) -> int | None:
    ttl = await redis.ttl(key)
    if ttl < 0:
        return None
    return ttl


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=False)


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
