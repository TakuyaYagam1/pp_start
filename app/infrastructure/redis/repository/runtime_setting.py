"""Redis adapter for per-chat runtime moderation settings"""

from __future__ import annotations

from redis.asyncio import Redis

from app.domain import ActionMode
from app.infrastructure.redis.client import redis_value_to_str

ACTION_MODE_KEY = "settings:action_mode"
ACTION_MODE_KEY_PREFIX = "settings:action_mode"
NOTIFICATION_TARGET_KEY_PREFIX = "settings:notification_target"


class RuntimeSettingsRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def action_mode_key(chat_id: int | None = None) -> str:
        if chat_id is None:
            return ACTION_MODE_KEY
        return f"{ACTION_MODE_KEY_PREFIX}:{chat_id}"

    async def get_action_mode(
        self,
        *,
        default: ActionMode,
        chat_id: int | None = None,
    ) -> ActionMode:
        key = self.action_mode_key(chat_id)
        raw = await self._redis.get(key)
        if raw is None and chat_id is not None:
            raw = await self._redis.get(ACTION_MODE_KEY)
            key = ACTION_MODE_KEY
        if raw is None:
            return default

        raw_text = redis_value_to_str(raw)
        if raw_text is None:
            return default

        try:
            return ActionMode(raw_text)
        except ValueError:
            await self._redis.delete(key)
            return default

    async def set_action_mode(
        self,
        action_mode: ActionMode,
        *,
        chat_id: int | None = None,
    ) -> None:
        await self._redis.set(self.action_mode_key(chat_id), action_mode.value)

    async def reset_action_mode(self, *, chat_id: int | None = None) -> None:
        if chat_id is None:
            await self._redis.delete(ACTION_MODE_KEY)
            return

        await self._redis.delete(self.action_mode_key(chat_id))
        await self._redis.delete(ACTION_MODE_KEY)

    @staticmethod
    def notification_target_key(chat_id: int) -> str:
        return f"{NOTIFICATION_TARGET_KEY_PREFIX}:{chat_id}"

    async def get_notification_target(self, *, chat_id: int) -> str | None:
        raw = await self._redis.get(self.notification_target_key(chat_id))
        return redis_value_to_str(raw)

    async def set_notification_target(self, *, chat_id: int, target: str) -> None:
        await self._redis.set(self.notification_target_key(chat_id), target)

    async def reset_notification_target(self, *, chat_id: int) -> None:
        await self._redis.delete(self.notification_target_key(chat_id))
