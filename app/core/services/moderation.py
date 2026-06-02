from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.config import Settings
from app.cache.redis import BlacklistRepository
from app.core.models import ModerationAction, SpamDetectionResult
from app.logging import get_logger, log_spam_event
from app.tg_bot.utils.telegram_api import call_telegram_api


class ModerationService:
    async def notify_admin_about_spam(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        settings: Settings,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id)
        message_text = str(getattr(message, "text", "") or "")
        event_logger = logger or get_logger("app")
        admin_target = _format_admin_target(settings)
        spammer = _format_spammer(message.from_user)
        message_reference = build_message_reference(message)
        notification_text = (
            f"{admin_target}, обнаружен спам.\n"
            f"Пользователь: {spammer}\n"
            f"user_id: {user_id}\n"
            f"Причина: {spam_result.reason}\n"
            f"Сообщение: {message_reference}\n"
            f"Текст: {message_text}"
        )
        send_kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": notification_text,
        }
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id is not None:
            send_kwargs["message_thread_id"] = int(message_thread_id)

        await call_telegram_api(
            operation=ModerationAction.NOTIFY_ADMIN.value,
            call=bot.send_message(**send_kwargs),
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
        )

        action = ModerationAction.NOTIFY_ADMIN.value
        details = (
            f"admin={admin_target}; "
            f"spammer={spammer}; "
            f"reason={spam_result.reason}; "
            f"llm_decision={spam_result.llm_decision.value}; "
            f"matched_term={spam_result.matched_term or '-'}"
        )
        log_spam_event(
            event_logger,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            action=action,
            details=details,
        )
        return replace(spam_result, moderation_action=ModerationAction.NOTIFY_ADMIN)

    async def delete_spam_message(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        blacklist_repository: BlacklistRepository,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id)
        message_id = int(message.message_id)
        message_text = str(getattr(message, "text", "") or "")
        event_logger = logger or get_logger("app")

        await call_telegram_api(
            operation=ModerationAction.DELETE_MESSAGE.value,
            call=bot.delete_message(chat_id=chat_id, message_id=message_id),
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
        )
        await call_telegram_api(
            operation=ModerationAction.BAN_UNBAN.value,
            call=bot.ban_chat_member(chat_id=chat_id, user_id=user_id),
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
        )
        await call_telegram_api(
            operation=ModerationAction.BAN_UNBAN.value,
            call=bot.unban_chat_member(chat_id=chat_id, user_id=user_id),
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
        )
        await blacklist_repository.add(chat_id=chat_id, user_id=user_id)

        action = ModerationAction.DELETE_MESSAGE.value
        details = (
            f"reason={spam_result.reason}; "
            f"llm_decision={spam_result.llm_decision.value}; "
            f"matched_term={spam_result.matched_term or '-'}"
        )
        log_spam_event(
            event_logger,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            action=action,
            details=details,
        )
        return replace(spam_result, moderation_action=ModerationAction.DELETE_MESSAGE)


def _format_admin_target(settings: Settings) -> str:
    if settings.admin_username:
        username = settings.admin_username.strip()
        return username if username.startswith("@") else f"@{username}"
    return f"admin_id:{settings.admin_id}"


def _format_spammer(from_user: Any) -> str:
    username = getattr(from_user, "username", None)
    user_id = int(from_user.id)
    if username:
        username = str(username).strip()
        formatted_username = username if username.startswith("@") else f"@{username}"
        return f"{formatted_username} ({user_id})"
    return str(user_id)


def build_message_reference(message: Any) -> str:
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = int(getattr(message, "message_id"))
    username = getattr(chat, "username", None)

    if username:
        public_username = str(username).strip().lstrip("@")
        if public_username:
            return f"https://t.me/{public_username}/{message_id}"

    return f"chat_id={chat_id}; message_id={message_id}"
