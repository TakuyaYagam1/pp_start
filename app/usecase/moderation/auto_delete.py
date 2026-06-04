"""Durable auto-delete scheduler for temporary Telegram messages"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.domain import AutoDeleteMessage, ModerationAction
from app.observability.logging import get_logger, log_app_event
from app.usecase.contract import AutoDeleteMessageStore


@dataclass
class AutoDeleteTaskRegistry:
    task: dict[tuple[int, int], asyncio.Task[bool]] = field(default_factory=dict)

    async def cancel_all(self) -> None:
        tasks = set(self.task.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task.clear()


async def schedule_auto_delete_message(
    *,
    bot: Any,
    auto_delete_message_repository: AutoDeleteMessageStore,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    chat_id: int,
    message_id: int,
    delay_seconds: float,
    user_id: int | None = None,
    logger: logging.Logger | None = None,
) -> AutoDeleteMessage:
    normalized_delay_seconds = max(0.0, delay_seconds)
    pending = await auto_delete_message_repository.create(
        chat_id=chat_id,
        message_id=message_id,
        delete_at=datetime.now(UTC) + timedelta(seconds=normalized_delay_seconds),
        user_id=user_id,
    )
    register_auto_delete_task(
        bot=bot,
        auto_delete_message_repository=auto_delete_message_repository,
        auto_delete_task_registry=auto_delete_task_registry,
        pending=pending,
        delay_seconds=normalized_delay_seconds,
        logger=logger,
    )
    return pending


def register_auto_delete_task(
    *,
    bot: Any,
    auto_delete_message_repository: AutoDeleteMessageStore,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    pending: AutoDeleteMessage,
    delay_seconds: float,
    logger: logging.Logger | None = None,
) -> asyncio.Task[bool]:
    key = (pending.chat_id, pending.message_id)
    existing_task = auto_delete_task_registry.task.pop(key, None)
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()

    task = asyncio.create_task(
        delete_auto_delete_message_after_delay(
            bot=bot,
            auto_delete_message_repository=auto_delete_message_repository,
            pending=pending,
            delay_seconds=delay_seconds,
            logger=logger,
        )
    )
    auto_delete_task_registry.task[key] = task

    def remove_completed_task(completed_task: asyncio.Task[bool]) -> None:
        if auto_delete_task_registry.task.get(key) is completed_task:
            auto_delete_task_registry.task.pop(key, None)
        log_failed_auto_delete_task(
            completed_task,
            pending=pending,
            logger=logger,
        )

    task.add_done_callback(remove_completed_task)
    return task


def log_failed_auto_delete_task(
    completed_task: asyncio.Task[bool],
    *,
    pending: AutoDeleteMessage,
    logger: logging.Logger | None,
) -> None:
    if completed_task.cancelled():
        return

    exception = completed_task.exception()
    if exception is None:
        return

    log_app_event(
        logger or get_logger("app"),
        event="auto_delete_message_task_failed",
        chat_id=pending.chat_id,
        user_id=pending.user_id,
        action=ModerationAction.DELETE_MESSAGE.value,
        details=(
            f"message_id={pending.message_id}; error_type={type(exception).__name__}"
        ),
        level=logging.ERROR,
    )


async def delete_auto_delete_message_after_delay(
    *,
    bot: Any,
    auto_delete_message_repository: AutoDeleteMessageStore,
    pending: AutoDeleteMessage,
    delay_seconds: float,
    logger: logging.Logger | None = None,
) -> bool:
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    event_logger = logger or get_logger("app")
    try:
        await call_telegram_api(
            operation=ModerationAction.DELETE_MESSAGE.value,
            call=bot.delete_message(
                chat_id=pending.chat_id,
                message_id=pending.message_id,
            ),
            chat_id=pending.chat_id,
            user_id=pending.user_id,
            logger=event_logger,
        )
        deleted = True
    except Exception:
        deleted = False

    await auto_delete_message_repository.delete(
        chat_id=pending.chat_id,
        message_id=pending.message_id,
    )
    return deleted


async def restore_auto_delete_message_tasks(
    *,
    bot: Any,
    auto_delete_message_repository: AutoDeleteMessageStore,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    logger: logging.Logger | None = None,
) -> int:
    restored = 0
    event_logger = logger or get_logger("app")
    for pending in await auto_delete_message_repository.list():
        try:
            delay_seconds = delay_until_auto_delete(pending)
        except TypeError, ValueError:
            await auto_delete_message_repository.delete(
                chat_id=pending.chat_id,
                message_id=pending.message_id,
            )
            log_app_event(
                event_logger,
                event="auto_delete_message_restore_skipped",
                chat_id=pending.chat_id,
                user_id=pending.user_id,
                action="delete_auto_delete_message",
                details=(f"message_id={pending.message_id}; reason=invalid_delete_at"),
            )
            continue
        register_auto_delete_task(
            bot=bot,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
            pending=pending,
            delay_seconds=delay_seconds,
            logger=event_logger,
        )
        restored += 1

    if restored:
        log_app_event(
            event_logger,
            event="auto_delete_messages_restored",
            action="restore_auto_delete_messages",
            details=f"restored={restored}",
        )
    return restored


def delay_until_auto_delete(pending: AutoDeleteMessage) -> float:
    delete_at = datetime.fromisoformat(pending.delete_at)
    if delete_at.tzinfo is None:
        delete_at = delete_at.replace(tzinfo=UTC)
    return max(0.0, (delete_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
