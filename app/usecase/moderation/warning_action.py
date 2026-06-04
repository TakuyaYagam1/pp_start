"""Warning moderation actions and temporary message cleanup scheduling"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.domain import ModerationAction, SpamDetectionResult
from app.observability.logging import log_app_event, log_spam_event
from app.usecase.contract import AutoDeleteMessageStore
from app.usecase.moderation.auto_delete import (
    AutoDeleteTaskRegistry,
    schedule_auto_delete_message,
)
from app.usecase.moderation.message import build_moderation_message_context
from app.usecase.moderation.stop_word_action import send_stop_word_warning


async def delete_message_after_delay(
    *,
    bot: Any,
    chat_id: int,
    message_id: int,
    delay_seconds: float,
    user_id: int,
    message_text: str,
    logger: logging.Logger,
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await call_telegram_api(
            operation=ModerationAction.DELETE_MESSAGE.value,
            call=bot.delete_message(chat_id=chat_id, message_id=message_id),
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=logger,
        )
    except Exception:
        return


async def schedule_warning_message_delete(
    *,
    bot: Any,
    chat_id: int,
    message_id: int,
    delay_seconds: float,
    user_id: int,
    message_text: str,
    auto_delete_message_repository: AutoDeleteMessageStore | None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None,
    logger: logging.Logger,
) -> None:
    if (
        auto_delete_message_repository is not None
        and auto_delete_task_registry is not None
    ):
        try:
            await schedule_auto_delete_message(
                bot=bot,
                auto_delete_message_repository=auto_delete_message_repository,
                auto_delete_task_registry=auto_delete_task_registry,
                chat_id=chat_id,
                message_id=message_id,
                delay_seconds=delay_seconds,
                user_id=user_id,
                logger=logger,
            )
            return
        except Exception as exc:
            log_app_event(
                logger,
                event="auto_delete_schedule_failed",
                chat_id=chat_id,
                user_id=user_id,
                message_text=message_text,
                action=ModerationAction.DELETE_MESSAGE.value,
                details=f"error_type={type(exc).__name__}; fallback=in_memory_task",
                level=logging.ERROR,
            )

    asyncio.create_task(
        delete_message_after_delay(
            bot=bot,
            chat_id=chat_id,
            message_id=message_id,
            delay_seconds=delay_seconds,
            user_id=user_id,
            message_text=message_text,
            logger=logger,
        )
    )


async def warn_stop_word_spam(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    warning_message_ttl_seconds: float | None = None,
    auto_delete_message_repository: AutoDeleteMessageStore | None = None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)
    matched_term = spam_result.matched_term or spam_result.stop_word.matched_term

    await call_telegram_api(
        operation=ModerationAction.DELETE_MESSAGE.value,
        call=bot.delete_message(
            chat_id=context.chat_id,
            message_id=context.message_id,
        ),
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        logger=context.logger,
    )
    warning_message = await send_stop_word_warning(
        bot=bot,
        message=message,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        matched_term=matched_term,
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

    details = (
        f"reason=stop_word_first_warning; "
        f"llm_decision={spam_result.llm_decision.value}; "
        f"matched_term={matched_term or '-'}"
    )
    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=ModerationAction.WARN_USER.value,
        details=details,
    )
    return replace(spam_result, moderation_action=ModerationAction.WARN_USER)
