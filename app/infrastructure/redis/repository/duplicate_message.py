"""Redis adapter for duplicate message and warning state"""

from __future__ import annotations

import hashlib
import json

from redis.asyncio import Redis

from app.domain import DuplicateMessageState
from app.infrastructure.redis.client import get_redis_json_model, redis_value_to_str

DUPLICATE_MESSAGE_KEY_PREFIX = "duplicate_message"
DUPLICATE_MESSAGE_WARNING_KEY_PREFIX = "duplicate_message_warning"
DUPLICATE_MESSAGE_WARNING_GRACE_KEY_PREFIX = "duplicate_message_warning_grace"
DUPLICATE_MESSAGE_MAX_TRACKED_IDS = 50
DUPLICATE_MESSAGE_RECORD_SCRIPT = """
local key = KEYS[1]
local message_id = tonumber(ARGV[1])
local digest = ARGV[2]
local user_id = tonumber(ARGV[3])
local chat_id = tonumber(ARGV[4])
local content_key = ARGV[5]
local ttl_seconds = tonumber(ARGV[6])
local max_tracked_ids = tonumber(ARGV[7])
if not max_tracked_ids or max_tracked_ids < 1 then
    max_tracked_ids = 1
end

local message_ids = {message_id}
local raw = redis.call("GET", key)
if raw then
    local ok, previous = pcall(cjson.decode, raw)
    if ok
        and type(previous) == "table"
        and previous["digest"] == digest
        and type(previous["message_ids"]) == "table"
    then
        message_ids = {}
        for _, previous_message_id in ipairs(previous["message_ids"]) do
            local normalized_message_id = tonumber(previous_message_id)
            if normalized_message_id then
                table.insert(message_ids, normalized_message_id)
            end
        end
        table.insert(message_ids, message_id)
    end
end

while #message_ids > max_tracked_ids do
    table.remove(message_ids, 1)
end

local state = {
    user_id = user_id,
    chat_id = chat_id,
    digest = digest,
    content_key = content_key,
    message_ids = message_ids,
}
local encoded = cjson.encode(state)
redis.call("SET", key, encoded, "EX", ttl_seconds)
return encoded
"""


class DuplicateMessageRepository:
    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        warning_ttl_seconds: int,
        warning_grace_seconds: int = 0,
        max_tracked_message_ids: int = DUPLICATE_MESSAGE_MAX_TRACKED_IDS,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._warning_ttl_seconds = warning_ttl_seconds
        self._warning_grace_seconds = warning_grace_seconds
        self._max_tracked_message_ids = max(1, max_tracked_message_ids)

    @staticmethod
    def normalize_content_key(content_key: str) -> str:
        return " ".join(content_key.casefold().split())

    @classmethod
    def digest_content_key(cls, content_key: str) -> str:
        normalized_content_key = cls.normalize_content_key(content_key)
        return hashlib.sha256(normalized_content_key.encode("utf-8")).hexdigest()

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"{DUPLICATE_MESSAGE_KEY_PREFIX}:{chat_id}:{user_id}"

    @staticmethod
    def warning_key(chat_id: int, user_id: int) -> str:
        return f"{DUPLICATE_MESSAGE_WARNING_KEY_PREFIX}:{chat_id}:{user_id}"

    @staticmethod
    def warning_grace_key(chat_id: int, user_id: int) -> str:
        return f"{DUPLICATE_MESSAGE_WARNING_GRACE_KEY_PREFIX}:{chat_id}:{user_id}"

    async def record_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        content_key: str,
    ) -> DuplicateMessageState:
        normalized_content_key = self.normalize_content_key(content_key)
        digest = self.digest_content_key(content_key)
        raw_state = await self._redis.eval(
            DUPLICATE_MESSAGE_RECORD_SCRIPT,
            1,
            self.key(chat_id, user_id),
            str(message_id),
            digest,
            str(user_id),
            str(chat_id),
            normalized_content_key,
            str(self._ttl_seconds),
            str(self._max_tracked_message_ids),
        )
        raw_text = redis_value_to_str(raw_state)
        if raw_text is None:
            raise RuntimeError("duplicate message record script returned empty state")
        return DuplicateMessageState.from_mapping(json.loads(raw_text))

    async def get(
        self,
        *,
        chat_id: int,
        user_id: int,
    ) -> DuplicateMessageState | None:
        key = self.key(chat_id, user_id)
        return await get_redis_json_model(
            self._redis,
            key,
            DuplicateMessageState.from_mapping,
        )

    async def clear(self, *, chat_id: int, user_id: int) -> None:
        await self._redis.delete(self.key(chat_id, user_id))

    async def get_warning_digest(self, *, chat_id: int, user_id: int) -> str | None:
        raw = await self._redis.get(self.warning_key(chat_id, user_id))
        return redis_value_to_str(raw)

    async def mark_warned(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> None:
        await self._redis.set(
            self.warning_key(chat_id, user_id),
            digest,
            ex=self._warning_ttl_seconds,
        )
        await self._mark_warning_grace(chat_id=chat_id, user_id=user_id)

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> bool:
        was_set = await self._redis.set(
            self.warning_key(chat_id, user_id),
            digest,
            ex=self._warning_ttl_seconds,
            nx=True,
        )
        if was_set:
            await self._mark_warning_grace(chat_id=chat_id, user_id=user_id)
        return bool(was_set)

    async def has_warning_grace(self, *, chat_id: int, user_id: int) -> bool:
        return bool(await self._redis.exists(self.warning_grace_key(chat_id, user_id)))

    async def clear_warning(self, *, chat_id: int, user_id: int) -> None:
        await self._redis.delete(self.warning_key(chat_id, user_id))
        await self._redis.delete(self.warning_grace_key(chat_id, user_id))

    async def _mark_warning_grace(self, *, chat_id: int, user_id: int) -> None:
        if self._warning_grace_seconds <= 0:
            return
        await self._redis.set(
            self.warning_grace_key(chat_id, user_id),
            "1",
            ex=self._warning_grace_seconds,
        )
