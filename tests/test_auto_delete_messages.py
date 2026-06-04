from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain import AutoDeleteMessage
from app.usecase.moderation import AutoDeleteTaskRegistry
from app.usecase.moderation.auto_delete import (
    register_auto_delete_task,
    restore_auto_delete_message_tasks,
    schedule_auto_delete_message,
)


@dataclass
class FakeBot:
    deleted_messages: list[dict[str, int]] = field(default_factory=list)

    async def delete_message(self, **kwargs: int) -> None:
        self.deleted_messages.append(kwargs)


@dataclass
class FakeAutoDeleteMessageRepository:
    pending_messages: list[AutoDeleteMessage] = field(default_factory=list)
    deleted_messages: list[dict[str, int]] = field(default_factory=list)

    async def create(
        self,
        *,
        chat_id: int,
        message_id: int,
        delete_at: datetime,
        user_id: int | None = None,
    ) -> AutoDeleteMessage:
        pending = AutoDeleteMessage(
            chat_id=chat_id,
            message_id=message_id,
            delete_at=delete_at.astimezone(UTC).isoformat(),
            user_id=user_id,
        )
        self.pending_messages.append(pending)
        return pending

    async def list(self) -> tuple[AutoDeleteMessage, ...]:
        return tuple(self.pending_messages)

    async def delete(self, *, chat_id: int, message_id: int) -> bool:
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        self.pending_messages = [
            pending
            for pending in self.pending_messages
            if pending.chat_id != chat_id or pending.message_id != message_id
        ]
        return True


@dataclass
class FailingDeleteAutoDeleteMessageRepository(FakeAutoDeleteMessageRepository):
    async def delete(self, *, chat_id: int, message_id: int) -> bool:
        raise RuntimeError("redis failed")


def test_schedule_auto_delete_persists_and_deletes_message() -> None:
    async def run() -> None:
        bot = FakeBot()
        repository = FakeAutoDeleteMessageRepository()
        registry = AutoDeleteTaskRegistry()

        pending = await schedule_auto_delete_message(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
            chat_id=-100123,
            message_id=999,
            delay_seconds=0,
            user_id=42,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert pending.user_id == 42
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 999}]
        assert repository.deleted_messages == [{"chat_id": -100123, "message_id": 999}]
        assert repository.pending_messages == []
        assert registry.task == {}

    asyncio.run(run())


def test_auto_delete_task_failure_clears_registry_entry() -> None:
    async def run() -> None:
        bot = FakeBot()
        repository = FailingDeleteAutoDeleteMessageRepository()
        registry = AutoDeleteTaskRegistry()

        await schedule_auto_delete_message(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
            chat_id=-100123,
            message_id=999,
            delay_seconds=0,
            user_id=42,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert registry.task == {}

    asyncio.run(run())


def test_restore_auto_delete_tasks_deletes_due_message_after_restart() -> None:
    async def run() -> None:
        bot = FakeBot()
        repository = FakeAutoDeleteMessageRepository(
            pending_messages=[
                AutoDeleteMessage(
                    chat_id=-100123,
                    message_id=999,
                    delete_at=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
                    user_id=42,
                )
            ]
        )
        registry = AutoDeleteTaskRegistry()

        restored = await restore_auto_delete_message_tasks(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
        )
        task = registry.task[(-100123, 999)]
        deleted = await task

        assert restored == 1
        assert deleted is True
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 999}]
        assert repository.pending_messages == []
        assert registry.task == {}

    asyncio.run(run())


def test_replaced_auto_delete_task_keeps_current_registry_entry() -> None:
    async def run() -> None:
        bot = FakeBot()
        repository = FakeAutoDeleteMessageRepository()
        registry = AutoDeleteTaskRegistry()
        pending = AutoDeleteMessage(
            chat_id=-100123,
            message_id=999,
            delete_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            user_id=42,
        )

        first_task = register_auto_delete_task(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
            pending=pending,
            delay_seconds=60,
        )
        second_task = register_auto_delete_task(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
            pending=pending,
            delay_seconds=60,
        )

        first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)
        await asyncio.sleep(0)

        assert registry.task[(-100123, 999)] is second_task

        await registry.cancel_all()
        assert second_task.cancelled()

    asyncio.run(run())


def test_restore_auto_delete_tasks_discards_invalid_delete_at() -> None:
    async def run() -> None:
        bot = FakeBot()
        repository = FakeAutoDeleteMessageRepository(
            pending_messages=[
                AutoDeleteMessage(
                    chat_id=-100123,
                    message_id=999,
                    delete_at="not-a-date",
                    user_id=42,
                )
            ]
        )
        registry = AutoDeleteTaskRegistry()

        restored = await restore_auto_delete_message_tasks(
            bot=bot,
            auto_delete_message_repository=repository,
            auto_delete_task_registry=registry,
        )

        assert restored == 0
        assert registry.task == {}
        assert repository.deleted_messages == [{"chat_id": -100123, "message_id": 999}]

    asyncio.run(run())
