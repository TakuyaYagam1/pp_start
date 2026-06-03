from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from app.config import Settings
from app.cache.redis import BlacklistRepository
from app.core.models import ModerationAction, SpamDetectionResult
from app.observability.logging import get_logger, log_spam_event
from app.tg_bot.utils.telegram_api import call_telegram_api


@dataclass(frozen=True)
class NotificationTarget:
    kind: str
    value: str


class ModerationService:
    async def notify_admin_about_spam(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        settings: Settings,
        notification_target: str | None = None,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id)
        message_text = str(getattr(message, "text", "") or "")
        event_logger = logger or get_logger("app")
        admin_target = _resolve_notification_target(
            settings=settings,
            runtime_target=notification_target,
        )
        admin_target_text = _format_admin_target(admin_target)
        spammer = _format_spammer(message.from_user)
        message_reference = build_message_reference(message)
        notification_text = (
            f"{admin_target_text}, обнаружен спам.\n"
            f"Пользователь: {spammer}\n"
            f"user_id: {user_id}\n"
            f"Причина: {spam_result.reason}\n"
            f"Сообщение: {message_reference}\n"
            f"Текст: {message_text}"
        )
        await _send_admin_notification(
            bot=bot,
            target=admin_target,
            group_chat_id=chat_id,
            message=message,
            text=notification_text,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
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

    async def warn_duplicate_flood(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        duplicate_message_ids: tuple[int, ...],
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id)
        message_text = str(getattr(message, "text", "") or "")
        event_logger = logger or get_logger("app")

        await _delete_messages(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            message_ids=duplicate_message_ids,
            logger=event_logger,
        )
        await _send_duplicate_warning(
            bot=bot,
            message=message,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=event_logger,
        )

        log_spam_event(
            event_logger,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            action=ModerationAction.WARN_USER.value,
            details=(
                f"reason={spam_result.reason}; "
                f"llm_decision={spam_result.llm_decision.value}; "
                f"matched_term={spam_result.matched_term or '-'}; "
                f"deleted_messages={len(duplicate_message_ids)}"
            ),
        )
        return replace(spam_result, moderation_action=ModerationAction.WARN_USER)

    async def kick_duplicate_flood(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        duplicate_message_ids: tuple[int, ...],
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id)
        message_text = str(getattr(message, "text", "") or "")
        event_logger = logger or get_logger("app")

        await _delete_messages(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            message_ids=duplicate_message_ids,
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

        log_spam_event(
            event_logger,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            action=ModerationAction.BAN_UNBAN.value,
            details=(
                f"reason={spam_result.reason}; "
                f"llm_decision={spam_result.llm_decision.value}; "
                f"matched_term={spam_result.matched_term or '-'}; "
                f"deleted_messages={len(duplicate_message_ids)}"
            ),
        )
        return replace(spam_result, moderation_action=ModerationAction.BAN_UNBAN)


async def _send_admin_notification(
    *,
    bot: Any,
    target: NotificationTarget,
    group_chat_id: int,
    message: Any,
    text: str,
    chat_id: int,
    user_id: int,
    message_text: str,
    logger: logging.Logger,
) -> None:
    send_kwargs: dict[str, Any] = {"text": text}
    if target.kind == "user_id":
        send_kwargs["chat_id"] = int(target.value)
    else:
        send_kwargs["chat_id"] = group_chat_id
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id is not None:
            send_kwargs["message_thread_id"] = int(message_thread_id)

    await call_telegram_api(
        operation=ModerationAction.NOTIFY_ADMIN.value,
        call=bot.send_message(**send_kwargs),
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        logger=logger,
    )


async def _delete_messages(
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


async def _send_duplicate_warning(
    *,
    bot: Any,
    message: Any,
    chat_id: int,
    user_id: int,
    message_text: str,
    logger: logging.Logger,
) -> None:
    send_kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": (
            "⚠️ Обнаружены одинаковые сообщения подряд. "
            "Повторный flood приведет к исключению из группы."
        ),
    }
    message_thread_id = getattr(message, "message_thread_id", None)
    if message_thread_id is not None:
        send_kwargs["message_thread_id"] = int(message_thread_id)

    await call_telegram_api(
        operation=ModerationAction.WARN_USER.value,
        call=bot.send_message(**send_kwargs),
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        logger=logger,
    )


def _resolve_notification_target(
    *,
    settings: Settings,
    runtime_target: str | None,
) -> NotificationTarget:
    parsed_runtime_target = _parse_notification_target(runtime_target)
    if parsed_runtime_target is not None:
        return parsed_runtime_target

    if settings.admin_id is not None:
        return NotificationTarget(kind="user_id", value=str(settings.admin_id))

    if settings.admin_username:
        return NotificationTarget(
            kind="username",
            value=settings.admin_username.strip().lstrip("@"),
        )

    return NotificationTarget(kind="username", value="admin")


def _parse_notification_target(raw_target: str | None) -> NotificationTarget | None:
    if raw_target is None:
        return None

    target = raw_target.strip()
    if not target:
        return None

    if target.lstrip("-").isdigit():
        return NotificationTarget(kind="user_id", value=str(int(target)))

    return NotificationTarget(kind="username", value=target.lstrip("@"))


def _format_admin_target(target: NotificationTarget) -> str:
    if target.kind == "user_id":
        return f"admin_id:{target.value}"
    return f"@{target.value}"


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
