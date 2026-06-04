"""Telegram actions for duplicate flood moderation flow"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.bot.util.telegram_message import (
    build_send_message_kwargs,
    message_thread_id_from_message,
)
from app.domain import ModerationAction, SpamDetectionResult
from app.observability.logging import log_spam_event
from app.usecase.contract import AutoDeleteMessageStore
from app.usecase.moderation.auto_delete import AutoDeleteTaskRegistry
from app.usecase.moderation.message import build_moderation_message_context
from app.usecase.moderation.warning_action import schedule_warning_message_delete

DUPLICATE_FLOOD_WARNING_TEXT = (
    "⚠️ Обнаружены одинаковые сообщения подряд. "
    "Повторный flood приведет к исключению из группы."
)


def build_duplicate_flood_log_details(
    spam_result: SpamDetectionResult,
    *,
    deleted_message_count: int,
) -> str:
    return (
        f"reason={spam_result.reason}; "
        f"llm_decision={spam_result.llm_decision.value}; "
        f"matched_term={spam_result.matched_term or '-'}; "
        f"deleted_messages={deleted_message_count}"
    )


async def delete_messages(
    *,
    bot: Any,
    chat_id: int,
    user_id: int,
    message_text: str,
    message_ids: tuple[int, ...],
    logger: logging.Logger,
) -> None:
    for message_id in dict.fromkeys(message_ids):
        try:
            await call_telegram_api(
                operation=ModerationAction.DELETE_MESSAGE.value,
                call=bot.delete_message(chat_id=chat_id, message_id=int(message_id)),
                chat_id=chat_id,
                user_id=user_id,
                message_text=message_text,
                logger=logger,
            )
        except Exception:
            continue


async def send_duplicate_warning(
    *,
    bot: Any,
    message: Any,
    chat_id: int,
    user_id: int,
    message_text: str,
    logger: logging.Logger,
) -> Any:
    send_kwargs = build_send_message_kwargs(
        chat_id=chat_id,
        text=DUPLICATE_FLOOD_WARNING_TEXT,
        message_thread_id=message_thread_id_from_message(message),
    )

    return await call_telegram_api(
        operation=ModerationAction.WARN_USER.value,
        call=bot.send_message(**send_kwargs),
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        logger=logger,
    )


async def warn_duplicate_flood(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    duplicate_message_ids: tuple[int, ...],
    warning_message_ttl_seconds: float | None = None,
    auto_delete_message_repository: AutoDeleteMessageStore | None = None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)

    await delete_messages(
        bot=bot,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        message_ids=duplicate_message_ids,
        logger=context.logger,
    )
    warning_message = await send_duplicate_warning(
        bot=bot,
        message=message,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        logger=context.logger,
    )
    warning_message_id = getattr(warning_message, "message_id", None)
    if warning_message_id is not None and warning_message_ttl_seconds is not None:
        await schedule_warning_message_delete(
            bot=bot,
            chat_id=context.chat_id,
            message_id=int(warning_message_id),
            delay_seconds=warning_message_ttl_seconds,
            user_id=context.user_id,
            message_text=context.message_text,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
            logger=context.logger,
        )

    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=ModerationAction.WARN_USER.value,
        details=build_duplicate_flood_log_details(
            spam_result,
            deleted_message_count=len(duplicate_message_ids),
        ),
    )
    return replace(spam_result, moderation_action=ModerationAction.WARN_USER)


async def delete_duplicate_flood_during_grace(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    duplicate_message_ids: tuple[int, ...],
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)

    await delete_messages(
        bot=bot,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        message_ids=duplicate_message_ids,
        logger=context.logger,
    )

    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=ModerationAction.DELETE_MESSAGE.value,
        details=build_duplicate_flood_log_details(
            spam_result,
            deleted_message_count=len(duplicate_message_ids),
        ),
    )
    return replace(spam_result, moderation_action=ModerationAction.DELETE_MESSAGE)


async def kick_duplicate_flood(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    duplicate_message_ids: tuple[int, ...],
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)

    await delete_messages(
        bot=bot,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        message_ids=duplicate_message_ids,
        logger=context.logger,
    )
    await call_telegram_api(
        operation=ModerationAction.BAN_UNBAN.value,
        call=bot.ban_chat_member(chat_id=context.chat_id, user_id=context.user_id),
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        logger=context.logger,
    )
    await call_telegram_api(
        operation=ModerationAction.BAN_UNBAN.value,
        call=bot.unban_chat_member(chat_id=context.chat_id, user_id=context.user_id),
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        logger=context.logger,
    )

    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=ModerationAction.BAN_UNBAN.value,
        details=build_duplicate_flood_log_details(
            spam_result,
            deleted_message_count=len(duplicate_message_ids),
        ),
    )
    return replace(spam_result, moderation_action=ModerationAction.BAN_UNBAN)
