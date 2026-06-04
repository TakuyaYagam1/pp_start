from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain import ActionMode, AutoDeleteMessage
from app.infrastructure.redis import (
    AutoDeleteMessageRepository,
    DuplicateMessageRepository,
    LLMResultCacheRepository,
    PendingVerificationRepository,
    RuntimeSettingsRepository,
    StopWordWarningRepository,
    VerifiedUserRepository,
)
from app.infrastructure.redis.client import create_redis_client, redis_value_to_str


@dataclass
class FakeRedis:
    values: dict[str, str | bytes] = field(default_factory=dict)
    expirations: dict[str, int] = field(default_factory=dict)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        else:
            self.expirations.pop(key, None)
        return True

    async def get(self, key: str) -> str | bytes | None:
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

    async def scan_iter(self, match: str) -> object:
        prefix = match.removesuffix("*")
        for key in tuple(self.values):
            if key.startswith(prefix):
                yield key

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        message_id: str,
        digest: str,
        user_id: str,
        chat_id: str,
        content_key: str,
        ttl_seconds: str,
        max_tracked_ids: str = "50",
    ) -> str:
        message_ids = (int(message_id),)
        raw = self.values.get(key)
        raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if raw_text is not None:
            try:
                previous = json.loads(raw_text)
            except json.JSONDecodeError:
                previous = None
            if (
                isinstance(previous, dict)
                and previous.get("digest") == digest
                and isinstance(previous.get("message_ids"), list)
            ):
                previous_message_ids: list[int] = []
                for item in previous.get("message_ids", ()):
                    try:
                        previous_message_ids.append(int(item))
                    except TypeError:
                        continue
                    except ValueError:
                        continue
                message_ids = (*previous_message_ids, int(message_id))

        max_tracked = max(1, int(max_tracked_ids))
        message_ids = message_ids[-max_tracked:]

        state = {
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "digest": digest,
            "content_key": content_key,
            "message_ids": message_ids,
        }
        encoded = json.dumps(state, ensure_ascii=False)
        await self.set(key, encoded, ex=int(ttl_seconds))
        return encoded


def test_create_redis_client_uses_binary_response_mode() -> None:
    redis = create_redis_client("redis://localhost:6379/0")

    assert redis.get_encoder().decode_responses is False


def test_redis_value_to_str_replaces_invalid_utf8_bytes() -> None:
    assert redis_value_to_str(b"ok") == "ok"
    assert redis_value_to_str(b"\xff") == "\ufffd"


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


def test_pending_verification_repository_discards_invalid_payload() -> None:
    async def run() -> None:
        redis = FakeRedis(
            values={"verify:-100123:42": "{not-json"},
            expirations={"verify:-100123:42": 180},
        )
        repository = PendingVerificationRepository(redis, ttl_seconds=180)

        assert await repository.get(chat_id=-100123, user_id=42) is None
        assert "verify:-100123:42" not in redis.values
        assert "verify:-100123:42" not in redis.expirations

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


def test_auto_delete_message_repository_lists_and_deletes_pending_job() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = AutoDeleteMessageRepository(redis, cleanup_grace_seconds=120)
        delete_at = datetime.now(UTC) + timedelta(seconds=60)

        pending = await repository.create(
            chat_id=-100123,
            message_id=999,
            delete_at=delete_at,
            user_id=42,
        )

        key = "auto_delete_message:-100123:999"
        assert key in redis.values
        assert 120 < redis.expirations[key] <= 180
        assert pending == AutoDeleteMessage(
            chat_id=-100123,
            message_id=999,
            delete_at=pending.delete_at,
            user_id=42,
        )
        assert await repository.list() == (pending,)
        assert await repository.get_ttl(chat_id=-100123, message_id=999) is not None
        assert await repository.delete(chat_id=-100123, message_id=999) is True
        assert await repository.list() == ()

    asyncio.run(run())


def test_llm_cache_repository_uses_short_default_ttl() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = LLMResultCacheRepository(redis)

        await repository.set("казино промокод", "да")

        key = repository.key("казино промокод")
        assert redis.values[key] == "да"
        assert redis.expirations[key] == 300
        assert await repository.get("казино промокод") == "да"
        assert await repository.get_ttl("казино промокод") == 300

    asyncio.run(run())


def test_duplicate_message_repository_counts_same_text_and_resets_on_change() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        first = await repository.record_message(
            chat_id=-100123,
            user_id=42,
            message_id=1,
            content_key="text:Привет   Мир",
        )
        second = await repository.record_message(
            chat_id=-100123,
            user_id=42,
            message_id=2,
            content_key="text:привет мир",
        )
        changed = await repository.record_message(
            chat_id=-100123,
            user_id=42,
            message_id=3,
            content_key="text:другой текст",
        )

        assert first.message_ids == (1,)
        assert second.message_ids == (1, 2)
        assert changed.message_ids == (3,)
        assert redis.expirations["duplicate_message:-100123:42"] == 60

    asyncio.run(run())


def test_duplicate_message_repository_discards_invalid_payload() -> None:
    async def run() -> None:
        redis = FakeRedis(
            values={"duplicate_message:-100123:42": "{not-json"},
            expirations={"duplicate_message:-100123:42": 60},
        )
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        assert await repository.get(chat_id=-100123, user_id=42) is None
        assert "duplicate_message:-100123:42" not in redis.values
        assert "duplicate_message:-100123:42" not in redis.expirations

    asyncio.run(run())


def test_duplicate_message_repository_resets_corrupted_message_ids_on_record() -> None:
    async def run() -> None:
        digest = DuplicateMessageRepository.digest_content_key("text:same")
        redis = FakeRedis(
            values={
                "duplicate_message:-100123:42": json.dumps(
                    {
                        "user_id": 42,
                        "chat_id": -100123,
                        "digest": digest,
                        "content_key": "text:same",
                        "message_ids": "not-a-list",
                    },
                    ensure_ascii=False,
                )
            },
            expirations={"duplicate_message:-100123:42": 60},
        )
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        state = await repository.record_message(
            chat_id=-100123,
            user_id=42,
            message_id=2,
            content_key="text:same",
        )

        assert state.message_ids == (2,)
        assert state.digest == digest

    asyncio.run(run())


def test_duplicate_message_repository_resets_non_object_payload_on_record() -> None:
    async def run() -> None:
        redis = FakeRedis(
            values={"duplicate_message:-100123:42": json.dumps("not-a-state")},
            expirations={"duplicate_message:-100123:42": 60},
        )
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        state = await repository.record_message(
            chat_id=-100123,
            user_id=42,
            message_id=2,
            content_key="text:same",
        )

        assert state.message_ids == (2,)
        assert state.content_key == "text:same"

    asyncio.run(run())


def test_duplicate_message_repository_keeps_only_recent_message_ids() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
            max_tracked_message_ids=5,
        )

        for message_id in range(1, 11):
            state = await repository.record_message(
                chat_id=-100123,
                user_id=42,
                message_id=message_id,
                content_key="text:same",
            )

        assert state.message_ids == (6, 7, 8, 9, 10)

    asyncio.run(run())


def test_duplicate_message_repository_marks_and_clears_warning_digest() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        await repository.mark_warned(
            chat_id=-100123,
            user_id=42,
            digest="abc",
        )

        assert await repository.get_warning_digest(chat_id=-100123, user_id=42) == "abc"
        assert redis.expirations["duplicate_message_warning:-100123:42"] == 300
        assert await repository.has_warning_grace(chat_id=-100123, user_id=42) is True
        assert redis.expirations["duplicate_message_warning_grace:-100123:42"] == 3

        await repository.clear_warning(chat_id=-100123, user_id=42)

        assert await repository.get_warning_digest(chat_id=-100123, user_id=42) is None
        assert await repository.has_warning_grace(chat_id=-100123, user_id=42) is False

    asyncio.run(run())


def test_duplicate_message_repository_marks_warning_once() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = DuplicateMessageRepository(
            redis,
            ttl_seconds=60,
            warning_ttl_seconds=300,
            warning_grace_seconds=3,
        )

        first = await repository.mark_warned_once(
            chat_id=-100123,
            user_id=42,
            digest="first",
        )
        second = await repository.mark_warned_once(
            chat_id=-100123,
            user_id=42,
            digest="second",
        )

        assert first is True
        assert second is False
        assert await repository.get_warning_digest(chat_id=-100123, user_id=42) == (
            "first"
        )
        assert redis.expirations["duplicate_message_warning:-100123:42"] == 300
        assert redis.expirations["duplicate_message_warning_grace:-100123:42"] == 3

    asyncio.run(run())


def test_stop_word_warning_repository_marks_warning_once() -> None:
    async def run() -> None:
        redis = FakeRedis()
        repository = StopWordWarningRepository(redis, ttl_seconds=300)

        first = await repository.mark_warned_once(
            chat_id=-100123,
            user_id=42,
            matched_term="казино",
        )
        second = await repository.mark_warned_once(
            chat_id=-100123,
            user_id=42,
            matched_term="крипта",
        )

        assert first is True
        assert second is False
        assert await repository.get_warned_term(chat_id=-100123, user_id=42) == (
            "казино"
        )
        assert redis.expirations["stop_word_warning:-100123:42"] == 300

        await repository.clear(chat_id=-100123, user_id=42)

        assert await repository.get_warned_term(chat_id=-100123, user_id=42) is None

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


def test_runtime_settings_repository_scopes_action_mode_by_chat() -> None:
    async def run() -> None:
        redis = FakeRedis(values={"settings:action_mode": "notify_admin"})
        repository = RuntimeSettingsRepository(redis)

        await repository.set_action_mode(ActionMode.DELETE, chat_id=-100123)

        assert redis.values["settings:action_mode:-100123"] == "delete"
        assert (
            await repository.get_action_mode(
                default=ActionMode.NOTIFY_ADMIN,
                chat_id=-100123,
            )
            == ActionMode.DELETE
        )
        assert (
            await repository.get_action_mode(
                default=ActionMode.DELETE,
                chat_id=-100456,
            )
            == ActionMode.NOTIFY_ADMIN
        )

        await repository.reset_action_mode(chat_id=-100123)

        assert "settings:action_mode:-100123" not in redis.values
        assert (
            await repository.get_action_mode(
                default=ActionMode.DELETE,
                chat_id=-100123,
            )
            == ActionMode.DELETE
        )

    asyncio.run(run())


def test_runtime_settings_repository_chat_reset_clears_legacy_global_action_mode() -> (
    None
):
    async def run() -> None:
        redis = FakeRedis(values={"settings:action_mode": "notify_admin"})
        repository = RuntimeSettingsRepository(redis)

        await repository.set_action_mode(ActionMode.DELETE, chat_id=-100123)
        await repository.reset_action_mode(chat_id=-100123)

        assert "settings:action_mode:-100123" not in redis.values
        assert "settings:action_mode" not in redis.values
        assert (
            await repository.get_action_mode(
                default=ActionMode.DELETE,
                chat_id=-100123,
            )
            == ActionMode.DELETE
        )

    asyncio.run(run())


def test_runtime_settings_repository_reads_bytes_action_mode() -> None:
    async def run() -> None:
        redis = FakeRedis(values={"settings:action_mode": b"delete"})
        repository = RuntimeSettingsRepository(redis)

        assert (
            await repository.get_action_mode(default=ActionMode.NOTIFY_ADMIN)
            == ActionMode.DELETE
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


def test_runtime_settings_repository_reads_writes_and_resets_notification_target() -> (
    None
):
    async def run() -> None:
        redis = FakeRedis()
        repository = RuntimeSettingsRepository(redis)

        assert await repository.get_notification_target(chat_id=-100123) is None

        await repository.set_notification_target(
            chat_id=-100123,
            target="1242888754",
        )

        assert redis.values["settings:notification_target:-100123"] == "1242888754"
        assert await repository.get_notification_target(chat_id=-100123) == "1242888754"

        await repository.reset_notification_target(chat_id=-100123)

        assert "settings:notification_target:-100123" not in redis.values

    asyncio.run(run())
