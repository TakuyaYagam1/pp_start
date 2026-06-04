"""Spam moderation actions for delete and notify flows"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.config import Settings
from app.domain import ModerationAction, SpamDetectionResult
from app.observability.logging import log_spam_event
from app.usecase.moderation.message import (
    build_message_reference,
    build_moderation_message_context,
    build_spam_notification_text,
    format_spammer,
)
from app.usecase.moderation.notification import (
    format_admin_target,
    resolve_notification_target,
    send_admin_notification,
)


async def notify_admin_about_spam(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    settings: Settings,
    notification_target: str | None = None,
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)
    admin_target = resolve_notification_target(
        settings=settings,
        runtime_target=notification_target,
    )
    admin_target_text = format_admin_target(admin_target)
    spammer = format_spammer(message.from_user)
    message_reference = build_message_reference(message)
    notification_text = build_spam_notification_text(
        admin_target_text=admin_target_text,
        spammer=spammer,
        user_id=context.user_id,
        reason=spam_result.reason,
        message_reference=message_reference,
        message_text=context.message_text,
    )
    await send_admin_notification(
        bot=bot,
        target=admin_target,
        group_chat_id=context.chat_id,
        message=message,
        text=notification_text,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        logger=context.logger,
    )

    action = ModerationAction.NOTIFY_ADMIN.value
    details = (
        f"admin={admin_target_text}; "
        f"spammer={spammer}; "
        f"reason={spam_result.reason}; "
        f"llm_decision={spam_result.llm_decision.value}; "
        f"matched_term={spam_result.matched_term or '-'}"
    )
    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=action,
        details=details,
    )
    return replace(spam_result, moderation_action=ModerationAction.NOTIFY_ADMIN)


async def delete_spam_message(
    *,
    bot: Any,
    message: Any,
    spam_result: SpamDetectionResult,
    logger: logging.Logger | None = None,
) -> SpamDetectionResult:
    context = build_moderation_message_context(message, logger=logger)

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

    action = ModerationAction.DELETE_MESSAGE.value
    details = (
        f"reason={spam_result.reason}; "
        f"llm_decision={spam_result.llm_decision.value}; "
        f"matched_term={spam_result.matched_term or '-'}"
    )
    log_spam_event(
        context.logger,
        chat_id=context.chat_id,
        user_id=context.user_id,
        message_text=context.message_text,
        action=action,
        details=details,
    )
    return replace(spam_result, moderation_action=ModerationAction.DELETE_MESSAGE)
