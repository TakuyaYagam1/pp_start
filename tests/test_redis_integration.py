"""Redis repository integration tests against a real Redis container"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from app.domain import ActionMode
from app.infrastructure.redis import (
    AutoDeleteMessageRepository,
    DuplicateMessageRepository,
    LLMResultCacheRepository,
    PendingVerificationRepository,
    RuntimeSettingsRepository,
    StopWordWarningRepository,
    VerifiedUserRepository,
)

REDIS_IMAGE = "redis:8.8.0-alpine3.23"


@pytest.fixture(scope="module")
def redis_endpoint() -> Iterator[tuple[str, int]]:
    with RedisContainer(image=REDIS_IMAGE) as container:
        yield (
            container.get_container_host_ip(),
            int(container.get_exposed_port(6379)),
        )


def redis_client(redis_endpoint: tuple[str, int]) -> Redis:
    host, port = redis_endpoint
    return Redis(host=host, port=port, decode_responses=True)


def test_redis_repositories_persist_verification_state(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            pending_repository = PendingVerificationRepository(redis, ttl_seconds=120)
            verified_repository = VerifiedUserRepository(redis)

            pending = await pending_repository.create(
                user_id=42,
                chat_id=-100123,
                verification_message_id=777,
                message_thread_id=555,
                verification_chat_id=4242,
            )

            stored = await pending_repository.get(chat_id=-100123, user_id=42)
            ttl = await pending_repository.get_ttl(chat_id=-100123, user_id=42)
            assert stored == pending
            assert ttl is not None
            assert 0 < ttl <= 120

            await verified_repository.mark_verified(chat_id=-100123, user_id=42)

            assert await verified_repository.is_verified(
                chat_id=-100123,
                user_id=42,
            )
            assert await pending_repository.delete(chat_id=-100123, user_id=42)
            assert await pending_repository.get(chat_id=-100123, user_id=42) is None
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_redis_repository_persists_auto_delete_messages(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            repository = AutoDeleteMessageRepository(
                redis,
                cleanup_grace_seconds=120,
            )
            pending = await repository.create(
                chat_id=-100123,
                message_id=999,
                delete_at=datetime.now(UTC) + timedelta(seconds=60),
                user_id=42,
            )

            assert await repository.list() == (pending,)
            ttl = await repository.get_ttl(chat_id=-100123, message_id=999)
            assert ttl is not None
            assert 0 < ttl <= 180
            assert await repository.delete(chat_id=-100123, message_id=999)
            assert await repository.list() == ()
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_redis_repositories_use_real_nx_and_ttl_semantics(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
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
                content_key="text:hello   world",
            )
            second = await repository.record_message(
                chat_id=-100123,
                user_id=42,
                message_id=2,
                content_key="text:HELLO world",
            )

            assert first.message_ids == (1,)
            assert second.message_ids == (1, 2)

            assert await repository.mark_warned_once(
                chat_id=-100123,
                user_id=42,
                digest=second.digest,
            )
            assert not await repository.mark_warned_once(
                chat_id=-100123,
                user_id=42,
                digest="another-digest",
            )

            warning_ttl = await redis.ttl(repository.warning_key(-100123, 42))
            warning_grace_ttl = await redis.ttl(
                repository.warning_grace_key(-100123, 42)
            )
            state_ttl = await redis.ttl(repository.key(-100123, 42))
            assert 0 < warning_ttl <= 300
            assert 0 < warning_grace_ttl <= 3
            assert 0 < state_ttl <= 60
            assert (
                await repository.get_warning_digest(
                    chat_id=-100123,
                    user_id=42,
                )
                == second.digest
            )
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_duplicate_message_repository_records_parallel_messages_atomically(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            repository = DuplicateMessageRepository(
                redis,
                ttl_seconds=60,
                warning_ttl_seconds=300,
                warning_grace_seconds=3,
            )

            await asyncio.gather(
                *(
                    repository.record_message(
                        chat_id=-100123,
                        user_id=42,
                        message_id=message_id,
                        content_key="sticker:same-sticker",
                    )
                    for message_id in range(1, 21)
                )
            )

            stored = await repository.get(chat_id=-100123, user_id=42)

            assert stored is not None
            assert len(stored.message_ids) == 20
            assert set(stored.message_ids) == set(range(1, 21))
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_duplicate_message_repository_resets_corrupted_message_ids_in_real_redis(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            repository = DuplicateMessageRepository(
                redis,
                ttl_seconds=60,
                warning_ttl_seconds=300,
                warning_grace_seconds=3,
            )
            digest = repository.digest_content_key("text:same")
            await redis.set(
                repository.key(-100123, 42),
                json.dumps(
                    {
                        "user_id": 42,
                        "chat_id": -100123,
                        "digest": digest,
                        "content_key": "text:same",
                        "message_ids": "not-a-list",
                    },
                    ensure_ascii=False,
                ),
                ex=60,
            )

            state = await repository.record_message(
                chat_id=-100123,
                user_id=42,
                message_id=2,
                content_key="text:same",
            )

            assert state.message_ids == (2,)
            assert state.digest == digest
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_duplicate_message_repository_resets_non_object_payload_in_real_redis(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            repository = DuplicateMessageRepository(
                redis,
                ttl_seconds=60,
                warning_ttl_seconds=300,
                warning_grace_seconds=3,
            )
            await redis.set(
                repository.key(-100123, 42),
                json.dumps("not-a-state"),
                ex=60,
            )

            state = await repository.record_message(
                chat_id=-100123,
                user_id=42,
                message_id=2,
                content_key="text:same",
            )

            assert state.message_ids == (2,)
            assert state.content_key == "text:same"
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_duplicate_message_repository_keeps_only_recent_message_ids_in_real_redis(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
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
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_redis_repositories_persist_runtime_settings_and_llm_cache(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            settings_repository = RuntimeSettingsRepository(redis)
            cache_repository = LLMResultCacheRepository(redis, ttl_seconds=300)

            await settings_repository.set_action_mode(
                ActionMode.DELETE,
                chat_id=-100123,
            )
            await settings_repository.set_notification_target(
                chat_id=-100123,
                target="@admin",
            )
            await cache_repository.set("casino promo", "yes")

            assert (
                await settings_repository.get_action_mode(
                    default=ActionMode.NOTIFY_ADMIN,
                    chat_id=-100123,
                )
                == ActionMode.DELETE
            )
            assert (
                await settings_repository.get_notification_target(chat_id=-100123)
                == "@admin"
            )
            assert await cache_repository.get("CASINO   promo") == "yes"

            cache_ttl = await cache_repository.get_ttl("casino promo")
            assert cache_ttl is not None
            assert 0 < cache_ttl <= 300
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_runtime_settings_chat_reset_clears_legacy_global_action_mode_in_real_redis(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
            repository = RuntimeSettingsRepository(redis)
            await redis.set("settings:action_mode", ActionMode.NOTIFY_ADMIN.value)

            await repository.set_action_mode(ActionMode.DELETE, chat_id=-100123)
            await repository.reset_action_mode(chat_id=-100123)

            assert await redis.get("settings:action_mode:-100123") is None
            assert await redis.get("settings:action_mode") is None
            assert (
                await repository.get_action_mode(
                    default=ActionMode.DELETE,
                    chat_id=-100123,
                )
                == ActionMode.DELETE
            )
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_redis_repositories_use_real_stop_word_warning_nx_and_ttl(
    redis_endpoint: tuple[str, int],
) -> None:
    async def run() -> None:
        redis = redis_client(redis_endpoint)
        try:
            await redis.flushdb()
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

            warning_ttl = await redis.ttl(repository.key(-100123, 42))
            assert first is True
            assert second is False
            assert 0 < warning_ttl <= 300
            assert await repository.get_warned_term(chat_id=-100123, user_id=42) == (
                "казино"
            )
        finally:
            await redis.aclose()

    asyncio.run(run())
