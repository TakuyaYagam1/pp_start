from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.cache.redis import (
    BlacklistRepository,
    PendingVerificationRepository,
    RuntimeSettingsRepository,
    VerifiedUserRepository,
)
from app.core.models import ActionMode


@dataclass
class FakeRedis:
    values: dict[str, str] = field(default_factory=dict)
    expirations: dict[str, int] = field(default_factory=dict)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        else:
            self.expirations.pop(key, None)
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def ttl(self, key: str) -> int:
        if key not in self.values:
            return -2
        return self.expirations.get(key, -1)


def test_pending_verification_repository_creates_record_with_ttl() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = PendingVerificationRepository(redis, ttl_seconds=180)

        pending = await repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=777,
            message_thread_id=555,
        )

        key = "verify:-100123:42"
        assert key in redis.values
        assert redis.expirations[key] == 180
        assert pending.user_id == 42
        assert pending.chat_id == -100123
        assert pending.verification_message_id == 777
        assert pending.message_thread_id == 555
        assert pending.verification_chat_id is None
        assert await repository.get_ttl(chat_id=-100123, user_id=42) == 180
        assert await repository.get(chat_id=-100123, user_id=42) == pending

    asyncio.run(run())


def test_pending_verification_repository_deletes_record() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = PendingVerificationRepository(redis, ttl_seconds=180)
        await repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=777,
            message_thread_id=None,
            verification_chat_id=4242,
        )

        assert await repository.delete(chat_id=-100123, user_id=42) is True
        assert await repository.get(chat_id=-100123, user_id=42) is None
        assert await repository.get_ttl(chat_id=-100123, user_id=42) is None

    asyncio.run(run())


def test_verified_user_repository_marks_and_reads_key() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = VerifiedUserRepository(redis)

        assert await repository.is_verified(chat_id=-100123, user_id=42) is False

        await repository.mark_verified(chat_id=-100123, user_id=42)

        assert redis.values["verified:-100123:42"] == "1"
        assert await repository.is_verified(chat_id=-100123, user_id=42) is True
        assert await repository.is_verified(chat_id=-100123, user_id=43) is False

    asyncio.run(run())


def test_blacklist_repository_adds_and_reads_key_without_ttl() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = BlacklistRepository(redis)

        assert await repository.contains(chat_id=-100123, user_id=42) is False

        await repository.add(chat_id=-100123, user_id=42)

        assert redis.values["blacklist:-100123:42"] == "1"
        assert "blacklist:-100123:42" not in redis.expirations
        assert await repository.contains(chat_id=-100123, user_id=42) is True
        assert await repository.contains(chat_id=-100123, user_id=43) is False

    asyncio.run(run())


def test_runtime_settings_repository_reads_writes_and_resets_action_mode() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = RuntimeSettingsRepository(redis)

        assert (
            await repository.get_action_mode(default=ActionMode.NOTIFY_ADMIN)
            == ActionMode.NOTIFY_ADMIN
        )

        await repository.set_action_mode(ActionMode.DELETE)

        assert redis.values["settings:action_mode"] == "delete"
        assert (
            await repository.get_action_mode(default=ActionMode.NOTIFY_ADMIN)
            == ActionMode.DELETE
        )

        await repository.reset_action_mode()

        assert "settings:action_mode" not in redis.values
        assert (
            await repository.get_action_mode(default=ActionMode.NOTIFY_ADMIN)
            == ActionMode.NOTIFY_ADMIN
        )

    asyncio.run(run())


def test_runtime_settings_repository_discards_invalid_action_mode() -> None:
    async def run() -> None:
        redis = FakeRedis(values={"settings:action_mode": "notify_admi"})
        repository = RuntimeSettingsRepository(redis)

        assert (
            await repository.get_action_mode(default=ActionMode.DELETE)
            == ActionMode.DELETE
        )
        assert "settings:action_mode" not in redis.values

    asyncio.run(run())
