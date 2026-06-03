from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.cache.redis import (
    BlacklistRepository,
    PendingVerificationRepository,
    VerifiedUserRepository,
)
from app.core.services.verification import (
    VERIFY_BUTTON_TEXT,
    VERIFY_SUCCESS_CALLBACK_ANSWER,
    VERIFY_SUCCESS_PRIVATE_MESSAGE,
    VerificationTaskRegistries,
    block_unverified_join_request_after_timeout,
    build_verification_message,
    build_verification_timeout_message,
    complete_verification_from_callback,
    remove_unverified_user_after_timeout,
    start_join_request_verification,
    start_member_verification,
)


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


@dataclass
class FakeBot:
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    deleted_messages: list[dict[str, int]] = field(default_factory=list)
    bans: list[dict[str, int]] = field(default_factory=list)
    unbans: list[dict[str, int]] = field(default_factory=list)
    restrictions: list[dict[str, Any]] = field(default_factory=list)
    approved_join_requests: list[dict[str, int]] = field(default_factory=list)
    declined_join_requests: list[dict[str, int]] = field(default_factory=list)

    async def send_message(self, **kwargs: Any) -> Any:
        self.sent_messages.append(kwargs)
        return SimpleNamespace(message_id=888)

    async def delete_message(self, **kwargs: int) -> None:
        self.deleted_messages.append(kwargs)

    async def ban_chat_member(self, **kwargs: int) -> None:
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: int) -> None:
        self.unbans.append(kwargs)

    async def restrict_chat_member(self, **kwargs: Any) -> None:
        self.restrictions.append(kwargs)

    async def approve_chat_join_request(self, **kwargs: int) -> None:
        self.approved_join_requests.append(kwargs)

    async def decline_chat_join_request(self, **kwargs: int) -> None:
        self.declined_join_requests.append(kwargs)


@dataclass
class FakeCallbackQuery:
    data: str
    from_user: Any
    message: Any
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, **kwargs: Any) -> None:
        self.answers.append(kwargs)


async def _sleep_forever() -> bool:
    await asyncio.sleep(60)
    return True


def test_verification_message_uses_clear_emoji_statuses() -> None:
    message = build_verification_message(
        user_id=42,
        user_full_name="Test User",
        timeout_seconds=180,
        chat_id=-100123,
    )

    assert message.text.startswith("⚠️ Test User")
    assert VERIFY_BUTTON_TEXT == "✅ Я человек"
    assert message.reply_markup.inline_keyboard[0][0].text == "✅ Я человек"
    assert "⏳ Осталось: 3:00" in message.text
    assert build_verification_timeout_message(timeout_seconds=180).startswith("❌")


def test_verification_task_registries_cancel_all_tasks() -> None:
    async def run() -> None:
        registries = VerificationTaskRegistries()
        timeout_task = asyncio.create_task(_sleep_forever())
        countdown_task = asyncio.create_task(_sleep_forever())
        registries.timeout_tasks[(-100123, 42)] = timeout_task
        registries.countdown_tasks[(-100123, 42)] = countdown_task

        await registries.cancel_all()

        assert registries.timeout_tasks == {}
        assert registries.countdown_tasks == {}
        assert timeout_task.cancelled()
        assert countdown_task.cancelled()

    asyncio.run(run())


def test_complete_verification_from_callback_marks_verified_and_cleans_pending() -> (
    None
):
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        verified_repository = VerifiedUserRepository(redis)
        await pending_repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=777,
            message_thread_id=555,
        )
        task = asyncio.create_task(_sleep_forever())
        task_registry = {(-100123, 42): task}
        callback = FakeCallbackQuery(
            data="verify_user:42",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat=SimpleNamespace(id=-100123)),
        )
        bot = FakeBot()

        completed = await complete_verification_from_callback(
            callback_query=callback,
            bot=bot,
            pending_verification_repository=pending_repository,
            verified_user_repository=verified_repository,
            task_registry=task_registry,
        )

        assert completed is True
        assert await pending_repository.get(chat_id=-100123, user_id=42) is None
        assert await verified_repository.is_verified(chat_id=-100123, user_id=42)
        assert bot.restrictions[0]["chat_id"] == -100123
        assert bot.restrictions[0]["user_id"] == 42
        assert bot.restrictions[0]["permissions"].can_send_messages is True
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 777}]
        assert bot.sent_messages == []
        assert callback.answers == [{"text": VERIFY_SUCCESS_CALLBACK_ANSWER}]
        assert task_registry == {}
        assert task.cancelled() or task.cancelling() > 0

    asyncio.run(run())


def test_start_member_verification_restricts_and_sends_group_challenge() -> None:
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        blacklist_repository = BlacklistRepository(redis)
        bot = FakeBot()
        task_registry: dict[tuple[int, int], asyncio.Task[bool]] = {}

        started = await start_member_verification(
            bot=bot,
            pending_verification_repository=pending_repository,
            blacklist_repository=blacklist_repository,
            chat_id=-100123,
            user_id=42,
            user_full_name="Test User",
            timeout_seconds=180,
            task_registry=task_registry,
        )

        assert started is True
        assert bot.restrictions[0]["chat_id"] == -100123
        assert bot.restrictions[0]["user_id"] == 42
        assert bot.restrictions[0]["permissions"].can_send_messages is False
        assert bot.sent_messages[0]["chat_id"] == -100123
        assert str(bot.sent_messages[0]["text"]).startswith("⚠️ Test User")
        assert await pending_repository.get(chat_id=-100123, user_id=42) is not None
        assert (-100123, 42) in task_registry

        for task in task_registry.values():
            task.cancel()

    asyncio.run(run())


def test_start_join_request_verification_sends_private_challenge() -> None:
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        blacklist_repository = BlacklistRepository(redis)
        task_registry: dict[tuple[int, int], asyncio.Task[bool]] = {}
        bot = FakeBot()

        started = await start_join_request_verification(
            bot=bot,
            pending_verification_repository=pending_repository,
            blacklist_repository=blacklist_repository,
            chat_id=-100123,
            user_id=42,
            user_chat_id=4242,
            user_full_name="Test User",
            timeout_seconds=180,
            task_registry=task_registry,
        )

        pending = await pending_repository.get(chat_id=-100123, user_id=42)
        assert started is True
        assert pending is not None
        assert pending.verification_message_id == 888
        assert pending.verification_chat_id == 4242
        assert bot.sent_messages[0]["chat_id"] == 4242
        assert str(bot.sent_messages[0]["text"]).startswith("⚠️ Test User")
        assert (
            bot.sent_messages[0]["reply_markup"].inline_keyboard[0][0].text
            == "✅ Я человек"
        )
        assert task_registry.keys() == {(-100123, 42)}

        task = task_registry.pop((-100123, 42))
        task.cancel()

    asyncio.run(run())


def test_join_request_callback_approves_user_and_deletes_private_challenge() -> None:
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        verified_repository = VerifiedUserRepository(redis)
        await pending_repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=888,
            verification_chat_id=4242,
        )
        task = asyncio.create_task(_sleep_forever())
        task_registry = {(-100123, 42): task}
        callback = FakeCallbackQuery(
            data="verify_user:-100123:42",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat=SimpleNamespace(id=4242)),
        )
        bot = FakeBot()

        completed = await complete_verification_from_callback(
            callback_query=callback,
            bot=bot,
            pending_verification_repository=pending_repository,
            verified_user_repository=verified_repository,
            task_registry=task_registry,
        )

        assert completed is True
        assert bot.approved_join_requests == [{"chat_id": -100123, "user_id": 42}]
        assert bot.deleted_messages == [{"chat_id": 4242, "message_id": 888}]
        assert bot.sent_messages == [
            {"chat_id": 4242, "text": VERIFY_SUCCESS_PRIVATE_MESSAGE}
        ]
        assert await pending_repository.get(chat_id=-100123, user_id=42) is None
        assert await verified_repository.is_verified(chat_id=-100123, user_id=42)
        assert callback.answers == [{"text": VERIFY_SUCCESS_CALLBACK_ANSWER}]
        assert task_registry == {}
        assert task.cancelled() or task.cancelling() > 0

    asyncio.run(run())


def test_timeout_removes_unverified_user_with_mocked_bot() -> None:
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        await pending_repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=777,
            message_thread_id=None,
        )
        bot = FakeBot()

        removed = await remove_unverified_user_after_timeout(
            bot=bot,
            pending_verification_repository=pending_repository,
            chat_id=-100123,
            user_id=42,
            timeout_seconds=0,
        )

        assert removed is True
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert await pending_repository.get(chat_id=-100123, user_id=42) is None

    asyncio.run(run())


def test_join_request_timeout_declines_bans_without_blacklist_and_cleans_pending() -> (
    None
):
    async def run() -> None:
        redis = FakeRedis()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=180)
        blacklist_repository = BlacklistRepository(redis)
        await pending_repository.create(
            user_id=42,
            chat_id=-100123,
            verification_message_id=888,
            verification_chat_id=4242,
        )
        bot = FakeBot()

        removed = await block_unverified_join_request_after_timeout(
            bot=bot,
            pending_verification_repository=pending_repository,
            blacklist_repository=blacklist_repository,
            chat_id=-100123,
            user_id=42,
            timeout_seconds=0,
        )

        assert removed is True
        assert bot.sent_messages == [
            {
                "chat_id": 4242,
                "text": build_verification_timeout_message(timeout_seconds=0),
            }
        ]
        assert bot.declined_join_requests == [{"chat_id": -100123, "user_id": 42}]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == []
        assert not await blacklist_repository.contains(chat_id=-100123, user_id=42)
        assert await pending_repository.get(chat_id=-100123, user_id=42) is None

    asyncio.run(run())
